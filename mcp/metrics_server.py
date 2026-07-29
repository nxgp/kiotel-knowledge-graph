#!/usr/bin/env python3
"""Kiotel graph MCP server with usage metrics + live dashboard.

Wraps graphify's Streamable-HTTP MCP ASGI app with a thin metrics layer:

  /mcp        -> graphify MCP server (unchanged protocol), every call recorded
  /dashboard  -> live usage dashboard (self-contained HTML, open to anyone),
                 with a Graph tab embedding /graph
  /graph      -> the interactive node/edge visualization (graphify's own
                 `graph export html` output, baked into the image as-is;
                 aggregated community view, since the full 7.8k-node graph
                 exceeds what's practical to render node-by-node in a browser)
  /stats      -> JSON aggregates powering the dashboard
  /healthz    -> liveness probe

What gets recorded per request (SQLite, METRICS_DB env, default /data/metrics.db):
  timestamp, JSON-RPC method, tool name, the human argument (question / label /
  source->target), latency ms, response bytes, ok/error, client (clientInfo from
  initialize, else User-Agent), and an estimated-tokens-saved figure.

Token-savings model (honest heuristic, shown as an estimate in the UI):
  a graph answer cites the source files it drew from (src=... / at=... markers).
  Without the graph, an agent answering the same question would have had to read
  those files. saved = (bytes of distinct cited files - response bytes) / 4.
  File sizes ship in the image as file-sizes.json, generated from build-root.
  This is a token-count estimate, not a dollar figure - no per-token price is
  assumed anywhere in this file, deliberately: any $/token rate is a business
  assumption for Kiotel to set, not a fact this server can assert.

Also tracked (all from real data, nothing simulated):
  - graph BUILD cost (one-time): real input/output tokens from the actual
    graphify extraction run, read from cost.json - contrasted against the
    cumulative ONGOING per-query savings above, for a rough build-vs-payoff
    picture in token terms.
  - p50/p95 tool-call latency (not just mean, which one slow call can hide).
  - distinct source files ever cited across all real queries ("coverage") out
    of the total corpus, plus the most-cited files - shows which parts of the
    codebase the graph is actually answering questions about.
  - avg tool calls per session (session depth, not just session count).

What this does NOT and CANNOT track: which underlying LLM/model is asking.
MCP's initialize handshake only carries clientInfo {name, version} for the
connecting tool (e.g. "claude-code"/"2.1") - never the model behind it. The
"Clients" panel is labelled accordingly; there is no fabricated model column.
"""
import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from graphify.serve import _build_http_app  # noqa: the wrapper is deliberately coupled

GRAPH_PATH = os.environ.get("GRAPH_PATH", "/app/graph.json")
DB_PATH = os.environ.get("METRICS_DB", "/data/metrics.db")
SIZES_PATH = os.environ.get("FILE_SIZES", "/app/file-sizes.json")
DASH_PATH = os.environ.get("DASHBOARD_HTML", "/app/dashboard.html")
GRAPH_VIZ_PATH = os.environ.get("GRAPH_VIZ_HTML", "/app/graph-viz.html")
COST_PATH = os.environ.get("BUILD_COST_JSON", "/app/cost.json")
MANIFEST_PATH = os.environ.get("CORPUS_MANIFEST", "/app/manifest.json")
PORT = int(os.environ.get("PORT", "8080"))
API_KEY = (os.environ.get("GRAPHIFY_API_KEY") or "").strip() or None

CITED_RE = re.compile(r"(?:src=|at=|source_file[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)")

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, method TEXT, tool TEXT,"
        " arg TEXT, latency_ms INTEGER, resp_bytes INTEGER, saved_tokens INTEGER,"
        " ok INTEGER, client TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cited_files ("
        " file TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, hits INTEGER DEFAULT 0)"
    )
    conn.commit()
    return conn


def _load_sizes() -> dict:
    try:
        return json.loads(Path(SIZES_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _graph_meta() -> dict:
    try:
        g = json.loads(Path(GRAPH_PATH).read_text(encoding="utf-8"))
        return {"nodes": len(g.get("nodes", [])),
                "edges": len(g.get("links", g.get("edges", [])))}
    except Exception:
        return {"nodes": 0, "edges": 0}


def _build_meta() -> dict:
    """One-time graph-extraction cost, from the real graphify cost.json (baked into the
    image at build time) - not an estimate. Distinct from per-query token savings below."""
    try:
        cost = json.loads(Path(COST_PATH).read_text(encoding="utf-8"))
    except Exception:
        cost = {"runs": [], "total_input_tokens": 0, "total_output_tokens": 0}
    try:
        corpus_files = len(json.loads(Path(MANIFEST_PATH).read_text(encoding="utf-8")))
    except Exception:
        corpus_files = 0
    runs = cost.get("runs", [])
    return {
        "build_input_tokens": cost.get("total_input_tokens", 0),
        "build_output_tokens": cost.get("total_output_tokens", 0),
        "build_runs": len(runs),
        "first_build_date": runs[0]["date"] if runs else None,
        "last_build_date": runs[-1]["date"] if runs else None,
        "corpus_files": corpus_files,
    }


SIZES = _load_sizes()
META = _graph_meta()
BUILD = _build_meta()
STARTED = datetime.now(timezone.utc).isoformat()
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
_db().close()


def _summarize_call(body: dict) -> tuple[str, str, str]:
    """(method, tool, arg) from a JSON-RPC request body."""
    method = body.get("method") or "?"
    tool, arg = "", ""
    if method == "tools/call":
        params = body.get("params") or {}
        tool = params.get("name") or "?"
        a = params.get("arguments") or {}
        arg = (a.get("question") or a.get("label") or a.get("node") or "")
        if not arg and ("source" in a or "target" in a):
            arg = f"{a.get('source', '?')} -> {a.get('target', '?')}"
        if not arg and "community_id" in a:
            arg = f"community {a['community_id']}"
    elif method == "initialize":
        info = (body.get("params") or {}).get("clientInfo") or {}
        arg = f"{info.get('name', '?')} {info.get('version', '')}".strip()
    return method, tool, str(arg)[:300]


def _cited_files(resp_text: str) -> set[str]:
    return {m for m in CITED_RE.findall(resp_text)}


def _saved_tokens(cited: set[str], resp_bytes: int) -> int:
    total = sum(SIZES.get(c, 0) for c in cited)
    return max(0, (total - resp_bytes) // 4)


def _record(method, tool, arg, latency_ms, resp_bytes, saved, ok, client, cited: set[str]) -> None:
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO events (ts, method, tool, arg, latency_ms, resp_bytes,"
            " saved_tokens, ok, client) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), method, tool, arg,
             latency_ms, resp_bytes, saved, 1 if ok else 0, client))
        now = datetime.now(timezone.utc).isoformat()
        for f in cited:
            conn.execute(
                "INSERT INTO cited_files (file, first_seen, last_seen, hits) VALUES (?,?,?,1)"
                " ON CONFLICT(file) DO UPDATE SET last_seen=excluded.last_seen, hits=hits+1",
                (f, now, now))
        conn.commit()
        conn.close()


def _percentiles(sorted_vals: list[int], pcts: tuple[float, ...]) -> dict[str, int]:
    if not sorted_vals:
        return {f"p{int(p * 100)}": 0 for p in pcts}
    out = {}
    for p in pcts:
        idx = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
        out[f"p{int(p * 100)}"] = sorted_vals[idx]
    return out


def _stats() -> dict:
    conn = _db()
    conn.row_factory = sqlite3.Row
    q = lambda sql, *p: [dict(r) for r in conn.execute(sql, p).fetchall()]  # noqa: E731
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    calls = "method='tools/call'"

    tool_calls = one(f"SELECT COUNT(*) FROM events WHERE {calls}")
    sessions = one("SELECT COUNT(*) FROM events WHERE method='initialize'")
    latencies = sorted(r[0] for r in conn.execute(
        f"SELECT latency_ms FROM events WHERE {calls} ORDER BY latency_ms").fetchall())
    corpus_files = BUILD.get("corpus_files") or 0
    files_reached = one("SELECT COUNT(*) FROM cited_files")

    out = {
        "graph": META, "build": BUILD, "started": STARTED,
        "totals": {
            "requests": one("SELECT COUNT(*) FROM events"),
            "tool_calls": tool_calls,
            "sessions": sessions,
            "avg_calls_per_session": round(tool_calls / sessions, 1) if sessions else 0,
            "errors": one("SELECT COUNT(*) FROM events WHERE ok=0"),
            "saved_tokens": one(f"SELECT COALESCE(SUM(saved_tokens),0) FROM events WHERE {calls}"),
            "avg_latency_ms": one(f"SELECT COALESCE(ROUND(AVG(latency_ms)),0) FROM events WHERE {calls}"),
            "today_calls": one(f"SELECT COUNT(*) FROM events WHERE {calls} AND ts >= date('now')"),
            "files_reached": files_reached,
            "corpus_files": corpus_files,
            **_percentiles(latencies, (0.5, 0.95)),
        },
        "by_tool": q(f"SELECT tool, COUNT(*) n, COALESCE(SUM(saved_tokens),0) saved"
                     f" FROM events WHERE {calls} GROUP BY tool ORDER BY n DESC"),
        "per_day": q(f"SELECT substr(ts,1,10) day, COUNT(*) n,"
                     f" COALESCE(SUM(saved_tokens),0) saved FROM events WHERE {calls}"
                     f" AND ts >= date('now','-13 days') GROUP BY day ORDER BY day"),
        "top_queries": q(f"SELECT arg, COUNT(*) n FROM events WHERE {calls} AND tool='query_graph'"
                         f" AND arg != '' GROUP BY arg ORDER BY n DESC LIMIT 10"),
        "clients": q("SELECT client, COUNT(*) n FROM events GROUP BY client ORDER BY n DESC LIMIT 8"),
        "top_files": q("SELECT file, hits FROM cited_files ORDER BY hits DESC LIMIT 8"),
        "recent": q(f"SELECT ts, tool, arg, latency_ms, saved_tokens, ok FROM events"
                    f" WHERE {calls} ORDER BY id DESC LIMIT 20"),
    }
    conn.close()
    return out


async def _read_body(receive):
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg)
        if msg["type"] != "http.request" or not msg.get("more_body"):
            break
    body = b"".join(m.get("body", b"") for m in chunks if m["type"] == "http.request")
    replayed = list(chunks)

    async def replay():
        if replayed:
            return replayed.pop(0)
        return await receive()

    return body, replay


def _respond(send, status, content, ctype="application/json"):
    async def go():
        payload = content if isinstance(content, (bytes, bytearray)) else content.encode()
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", ctype.encode()), (b"access-control-allow-origin", b"*")]})
        await send({"type": "http.response.body", "body": payload})
    return go()


DASHBOARD = Path(DASH_PATH).read_text(encoding="utf-8") if Path(DASH_PATH).exists() else "<h1>dashboard.html missing</h1>"
GRAPH_VIZ = (Path(GRAPH_VIZ_PATH).read_text(encoding="utf-8") if Path(GRAPH_VIZ_PATH).exists()
             else "<h1>graph-viz.html missing — run `graphify export html` and rebuild the image</h1>")

inner = _build_http_app(GRAPH_PATH, host="0.0.0.0", port=PORT, api_key=API_KEY,
                        path="/mcp", json_response=False, stateless=True,
                        session_timeout=3600.0)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        return await inner(scope, receive, send)
    if scope["type"] != "http":
        return await inner(scope, receive, send)

    path = scope.get("path", "/")
    if path in ("/", "/dashboard"):
        return await _respond(send, 200, DASHBOARD, "text/html; charset=utf-8")
    if path == "/graph":
        return await _respond(send, 200, GRAPH_VIZ, "text/html; charset=utf-8")
    if path == "/stats":
        data = await asyncio.to_thread(_stats)
        return await _respond(send, 200, json.dumps(data))
    if path == "/healthz":
        return await _respond(send, 200, '{"ok":true}')

    # everything else (i.e. /mcp) -> instrument and forward
    body, replay = await _read_body(receive)
    try:
        parsed = json.loads(body.decode() or "{}")
    except Exception:
        parsed = {}
    method, tool, arg = _summarize_call(parsed) if isinstance(parsed, dict) else ("?", "", "")
    ua = ""
    for k, v in scope.get("headers", []):
        if k == b"user-agent":
            ua = v.decode(errors="replace")[:120]
    client = arg if method == "initialize" else ua

    status_holder = {"status": 0}
    resp_chunks = []
    t0 = time.monotonic()

    async def send_wrapper(message):
        if message["type"] == "http.response.start":
            status_holder["status"] = message["status"]
        elif message["type"] == "http.response.body":
            b = message.get("body", b"")
            if b and sum(len(c) for c in resp_chunks) < 262_144:
                resp_chunks.append(b)
        await send(message)

    try:
        await inner(scope, replay, send_wrapper)
    finally:
        if isinstance(parsed, dict) and parsed.get("method"):
            latency = int((time.monotonic() - t0) * 1000)
            resp = b"".join(resp_chunks)
            text = resp.decode(errors="replace")
            cited = _cited_files(text) if method == "tools/call" else set()
            saved = _saved_tokens(cited, len(resp)) if method == "tools/call" else 0
            ok = (status_holder["status"] < 400 and '"error"' not in text[:2000]
                  and '"isError": true' not in text and '"isError":true' not in text)
            await asyncio.to_thread(_record, method, tool, arg, latency,
                                    len(resp), saved, ok, client, cited)


if __name__ == "__main__":
    import uvicorn
    print(f"kiotel graph MCP + metrics on :{PORT}  (/mcp, /dashboard, /stats)")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
