# Wiring the Kiotel graph as an MCP server

Once the graph is built (`graphify-out/graph.json`), each developer adds it to their AI
assistant so they can query the Kiotel codebase in natural language.

## Claude Code

Graphify registers a `/graphify` skill when you run `graphify install`, and also serves the
graph over MCP. Two ways to use it:

**A) The `/graphify` skill (simplest)** — after `graphify install`, point it at the committed
graph and query:

```
/graphify query "what connects the kiosk to the platform?"
/graphify path "kiosk_dashboard_windows_client/kiosk_Source/kiosk/frmMainScreen.cs" "kiosk_ai_services ingest"
/graphify explain "session_ai_audit"
```

**B) As an MCP server** — run:

```bash
python -m graphify.serve /absolute/path/to/graphify-out/graph.json
```

Then add it to your Claude Code MCP config (e.g. `~/.claude.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "kiotel-graph": {
      "command": "python",
      "args": ["-m", "graphify.serve", "/absolute/path/to/graphify-out/graph.json"]
    }
  }
}
```

For a shared/remote instance, serve over HTTP and point clients at the URL:

```bash
python -m graphify.serve /path/to/graph.json --transport http --port 8080
```

**C) Dockerized shared instance** — build once, everyone points at the URL:

```bash
docker compose up -d          # serves Streamable HTTP MCP on :8080
```

Claude Code config for the HTTP endpoint:

```json
{
  "mcpServers": {
    "kiotel-graph": {
      "type": "http",
      "url": "http://<host>:8080/mcp"
    }
  }
}
```

(If the server was started with `GRAPHIFY_API_KEY`, add `"headers": {"Authorization": "Bearer <key>"}`.)

## Claude chat (claude.ai) — team-wide custom connector

claude.ai talks to remote MCP servers directly; it needs a **public HTTPS** URL (the
dockerized server itself speaks plain HTTP, so deploy behind TLS — DO App Platform does
this automatically; see `.do/app.yaml`).

Each teammate (Pro/Max), or an admin once for the whole workspace (Team/Enterprise):

1. claude.ai → **Settings → Connectors → Add custom connector**
2. Name: `Kiotel Graph` · URL: `https://<your-app>.ondigitalocean.app/mcp`
3. Save. In a chat, enable it under the tools/search menu — then ask things like
   *"what connects the kiosk to the platform?"*

> **Auth caveat:** claude.ai custom connectors authenticate via OAuth or not at all — they
> cannot send a static `Authorization` header, so a `GRAPHIFY_API_KEY`-protected server
> won't work there. Deploying keyless means anyone with the URL can query the graph
> (internal architecture info — treat the URL as sensitive). Keep the API key OFF for the
> claude.ai deployment, or front it with an OAuth proxy later. Claude Code / Cursor DO
> support the API key via headers:
>
> ```json
> { "mcpServers": { "kiotel-graph": { "type": "http",
>     "url": "https://<your-app>.ondigitalocean.app/mcp",
>     "headers": { "Authorization": "Bearer <key>" } } } }
> ```

## Cursor / Codex / other MCP clients

Graphify ships assistant manifests via `graphify install`. For a generic MCP client, use the
same `python -m graphify.serve …` command as the server entry, or the HTTP endpoint above.

## Usage dashboard

The server tracks its own usage — open **`http://<host>:8080/dashboard`** (no auth) to see
live: tool calls, sessions, top questions, clients, latency, errors, and estimated tokens
saved. `/stats` returns the same as JSON. Metrics live in SQLite under the container's
`/data` (compose maps `./metrics-data/`).

## What to ask it

- "Where does a guest's ID scan data flow, end to end?"
- "What reads `property_rules`, and why?"
- "Which files handle the RELAY command protocol?"
- "What are the god-nodes / most-connected components in `kiosk_dashboard`?"
- "How does the platform tell the kiosk to start recording?"
- "What's the difference between the deployed audit engine and `conversation-intelligence`?"

## Query patterns & limits (read this before wiring agents)

Verified against the live server (graphify 0.9.25):

- `get_node`, `get_neighbors`, `shortest_path` take **`label`** (fuzzy-matched), not `node`/`id`. Wrong param name → validation error.
- `query_graph` answers are capped at a **~2,000-token budget** and say `TRUNCATED: showing N of M` when they overflow. Ask narrow questions, then drill in with `get_community` / `get_neighbors` on the hits.
- `shortest_path` caps at **8 hops**. For far-apart symbols, route through a known intermediate (e.g. the Socket.IO device link, the audit callback, `session_ai_audit`).
- Fuzzy start matching can pull lexical strays (asking about a DB "table" can match the UI `Table()` component). Prefer distinctive identifiers.
- Provenance is explicit on every edge: `EXTRACTED` (98%) is structural fact, `INFERRED` (2%) includes the curated cross-repo concept→code bridges, `AMBIGUOUS` is a flagged uncertainty.

## Known corpus issues (re-verified against the current repos, 2026-11-19)

- **SECURITY (history): a Deepgram API key was hardcoded** in `kiosk_ai_services/stt-tts-using-api-only/app/config.py`. The current file defaults `deepgram_api_key` to `""`, so the working tree is clean, but the old key is still in git history — rotate it if that has not been done. (It never appears in `graph.json`.)
- **SECURITY: `kiosk_ai_avatar/api_key_for_github.txt` is tracked in that repo's git.** The build deliberately excludes `api_key*` files and `*.env`, so it is not in the graph; the file itself still needs removing from the repo and the credential rotating.
- `kiosk_ai_services/stt-tts-using-api-only` still mentions ElevenLabs in its README and a few code comments while `app/core/transcriber.py` uses Deepgram; treat the README as stale. The graph keeps the reference edge as documentation of the discrepancy.
- `kiosk_dashboard/core/frontend/public` still ships the stock Next.js starter SVGs (`file`, `globe`, `next`, `vercel`, `window`).
- `kiosk_dashboard/core/backend/src/db/db_schema_view.ts` is **stale**: it does not document `session_ai_audit` or `session_events`, which migrations create (reported by `extract-db-schema-nodes.py`; the graph adds table nodes for both from the migrations).
- Doc concept nodes are named by the LLM pass, so ids like `kiosk_ai_services_audit_pipeline` are not stable across rebuilds. `scripts/bridges.json` was re-anchored for the 2026-11-19 rebuild; 47 of the 94 older concept→code bridges had no counterpart in the rewritten docs (or pointed at deleted code) and were dropped rather than guessed.
- `kiotel_dev_docs` is condensed to one API-catalog markdown per service, and includes the `kiotel_vision_ai_services` collection (that repo is graphed too, since 2026-11-20).
- `kiotel_channel_manager` is an early skeleton (about 20 files, no code-level links to other repos yet).

## Refresh

When the codebase changes, rebuild (`RUNBOOK.md` steps 2–4), commit the new `graphify-out/`,
and restart the MCP server. Consider a CI job so the graph stays current for everyone.
