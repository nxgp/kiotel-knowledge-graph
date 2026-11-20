#!/usr/bin/env python3
"""Smoke-test a running Kiotel graph MCP server with real queries.

Run it against the local server, the Docker container, or the deployed endpoint:

    python scripts/smoke-test-mcp.py                          # http://localhost:8080
    python scripts/smoke-test-mcp.py https://<host>           # deployed (add --api-key KEY)

It checks that the HTTP side is alive (/healthz, /stats, /dashboard, /graph), that all
10 MCP tools are exposed, and that a set of real cross-repo questions return answers that
cite the expected repos/files. Exits non-zero if any check fails, so it can gate a merge/deploy.

Start the server the same way production does (mcp/metrics_server.py), e.g. on Windows:
    $env:GRAPH_PATH="graphify-out\\graph.json"; $env:FILE_SIZES="graphify-out\\file-sizes.json"
    $env:CORPUS_MANIFEST="graphify-out\\manifest.json"; $env:BUILD_COST_JSON="graphify-out\\cost.json"
    $env:DASHBOARD_HTML="mcp\\dashboard.html"; $env:GRAPH_VIZ_HTML="graphify-out\\graph.html"
    $env:METRICS_DB="$env:TEMP\\metrics.db"; python mcp\\metrics_server.py
"""
import argparse
import asyncio
import json
import re
import sys
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

EXPECTED_TOOLS = {"query_graph", "get_node", "get_neighbors", "get_community", "god_nodes",
                  "graph_stats", "shortest_path", "list_prs", "get_pr_impact", "triage_prs"}

# (description, tool, arguments, regexes that must ALL appear in the answer)
CHECKS = [
    ("graph_stats reports a graph of the expected size",
     "graph_stats", {}, [r"\b(3\d,?\d{3})\b"]),
    ("payments -> PMS aggregator (folio client reaches the aggregator)",
     "shortest_path", {"source": "folio/client.ts", "target": "inbound/routes.ts"},
     [r"folio/client\.ts", r"pms_aggregator_service", r"inbound/routes\.ts"]),
    ("Space call token -> communications gateway (cross-repo contract)",
     "shortest_path", {"source": "call-token.routes.ts", "target": "httpChatAppGateway.js"},
     [r"call-token\.routes\.ts", r"httpChatAppGateway\.js"]),
    ("AI services -> transcription RunPod worker",
     # bare "rp_handler.py" is ambiguous (transcription AND vision workers): use the node id
     "shortest_path", {"source": "transcription_batches.py",
                       "target": "kiotel_transcription_service_src_rp_handler"},
     [r"transcription_batches\.py", r"rp_handler\.py"]),
    ("AI services -> vision RunPod worker (kiotel_vision_ai_services)",
     # two repos have an rp_handler.py, so name the vision one by its full path label
     "shortest_path", {"source": "vision_runpod_service.py",
                       "target": "kiotel_vision_ai_services/rp_handler.py"},
     [r"vision_runpod_service\.py", r"kiotel_vision_ai_services/rp_handler\.py"]),
    ("Space -> UI kit import",
     "shortest_path", {"source": "ns-shell.tsx", "target": "kiotel_ui_kit/src/index.ts"},
     [r"ns-shell\.tsx", r"ui_kit|ui-kit"]),
    ("what writes session_ai_audit? (DB table node + AI services/dashboard)",
     "query_graph", {"question": "what writes into session_ai_audit"},
     [r"session_ai_audit", r"kiosk_(ai_services|dashboard)"]),
    ("kiosk <-> platform link (Windows client talks to the dashboard)",
     "query_graph", {"question": "what connects the kiosk to the platform"},
     [r"kiosk_dashboard"]),
    ("PMS aggregator is queryable (new repo)",
     "query_graph", {"question": "how does the PMS aggregator provider adapter contract work"},
     [r"kiotel_pms_aggregator_service|aggregator"]),
    ("node lookup by label",
     "get_node", {"label": "session_ai_audit"}, [r"session_ai_audit"]),
    ("neighbors of a renamed repo's file (old graph called it kiotel_web)",
     "get_neighbors", {"label": "extOAuth.ts"}, [r"extOAuth|extoauth"]),
    ("god nodes are returned", "god_nodes", {"top_n": 5}, [r"\w"]),
]


def http_get(url: str, key: str | None):
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


async def run(base: str, key: str | None) -> int:
    fails = 0

    def report(ok: bool, label: str, detail: str = ""):
        nonlocal fails
        fails += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))

    print(f"Server: {base}")
    print("HTTP endpoints")
    for path in ("/healthz", "/stats", "/dashboard", "/graph"):
        try:
            status, body = http_get(base + path, None)
            report(status == 200 and len(body) > 0, f"GET {path}", f"{status}, {len(body):,} bytes")
        except Exception as e:  # noqa: BLE001 - report any failure, keep going
            report(False, f"GET {path}", repr(e))
    try:
        stats = json.loads(http_get(base + "/stats", None)[1])
        print(f"  /stats keys: {', '.join(list(stats)[:8])}")
    except Exception:  # noqa: BLE001
        pass

    headers = {"Authorization": f"Bearer {key}"} if key else None
    async with streamablehttp_client(base + "/mcp", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            print("MCP tools")
            report(EXPECTED_TOOLS <= tools, "all 10 tools exposed",
                   f"missing: {sorted(EXPECTED_TOOLS - tools)}" if EXPECTED_TOOLS - tools else "")
            print("Real queries")
            for desc, tool, args, patterns in CHECKS:
                try:
                    res = await s.call_tool(tool, args)
                    text = "\n".join(getattr(c, "text", "") for c in res.content)
                    missing = [p for p in patterns if not re.search(p, text, re.I)]
                    # The server echoes the query inside error text, so name-matching alone can
                    # pass on a failure: a path query must actually return a path.
                    if tool == "shortest_path" and not re.search(r"Shortest path \(\d+ hops?\)", text):
                        missing.append("an actual path (got: " + " ".join(text.split())[:60] + ")")
                    report(not res.isError and not missing, desc,
                           f"missing {missing}" if missing else "")
                    first = " ".join(text.split())[:220]
                    print(f"      {tool}({json.dumps(args)}) -> {first}")
                except Exception as e:  # noqa: BLE001
                    report(False, desc, repr(e))
    print(f"\nRESULT: {'PASS' if fails == 0 else f'FAIL ({fails} check(s))'}")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://localhost:8080")
    ap.add_argument("--api-key", default=None)
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.base.rstrip("/"), a.api_key)))
