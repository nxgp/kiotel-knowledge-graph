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

## Refresh

When the codebase changes, rebuild (`RUNBOOK.md` steps 2–4), commit the new `graphify-out/`,
and restart the MCP server. Consider a CI job so the graph stays current for everyone.
