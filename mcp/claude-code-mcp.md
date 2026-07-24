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
/graphify path "kiosk_Source/kiosk/frmMainScreen.cs" "audio_services ingest"
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

## Cursor / Codex / other MCP clients

Graphify ships assistant manifests via `graphify install`. For a generic MCP client, use the
same `python -m graphify.serve …` command as the server entry, or the HTTP endpoint above.

## What to ask it

- "Where does a guest's ID scan data flow, end to end?"
- "What reads `property_rules`, and why?"
- "Which files handle the RELAY command protocol?"
- "What are the god-nodes / most-connected components in `kiotel_web`?"
- "How does the platform tell the kiosk to start recording?"
- "What's the difference between the deployed audit engine and `conversation-intelligence`?"

## Query patterns & limits (read this before wiring agents)

Verified against the live server (graphify 0.9.25):

- `get_node`, `get_neighbors`, `shortest_path` take **`label`** (fuzzy-matched), not `node`/`id`. Wrong param name → validation error.
- `query_graph` answers are capped at a **~2,000-token budget** and say `TRUNCATED: showing N of M` when they overflow. Ask narrow questions, then drill in with `get_community` / `get_neighbors` on the hits.
- `shortest_path` caps at **8 hops**. For far-apart symbols, route through a known intermediate (e.g. the Socket.IO device link, the audit callback, `session_ai_audit`).
- Fuzzy start matching can pull lexical strays (asking about a DB "table" can match the UI `Table()` component). Prefer distinctive identifiers.
- Provenance is explicit on every edge: `EXTRACTED` (98%) is structural fact, `INFERRED` (2%) includes the curated cross-repo concept→code bridges, `AMBIGUOUS` is a flagged uncertainty.

## Known corpus issues (verified against source, 2026-07-23)

- **SECURITY: hardcoded Deepgram API key** at `audio_services/stt-tts-using-api-only/app/config.py:6`. Rotate the key and move it to env. (The key does **not** appear in `graph.json` or the extraction cache — verified.)
- `stt-tts-using-api-only` README claims ElevenLabs Scribe; the code exclusively uses **Deepgram nova-3**. The graph keeps this reference edge as documentation of the discrepancy.
- `kiotel_web` frontend ships the five stock Next.js starter SVGs unused (no source references them).

## Refresh

When the codebase changes, rebuild (`RUNBOOK.md` steps 2–4), commit the new `graphify-out/`,
and restart the MCP server. Consider a CI job so the graph stays current for everyone.
