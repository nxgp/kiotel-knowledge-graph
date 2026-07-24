# kiotel-knowledge-graph

A **combined knowledge graph of the entire Kiotel codebase** — built with
[Graphify](https://github.com/Graphify-Labs/graphify) across all four repos — served as a
**dockerized MCP server** so any dev's AI assistant (Claude Code, Cursor, Codex…) can query
how the Kiotel system fits together and *why*.

**Current graph** (deep-mode build, 2026-07-23): **7,770 nodes · 18,832 edges · 411
communities**, one connected component spanning all four repos (88% of nodes). Provenance
on every edge: 98% EXTRACTED (structural fact), 2% INFERRED (including 98 curated
concept→code bridges), 0% unresolved AMBIGUOUS.

> Ask "what connects the kiosk to the platform?", "where does a guest's ID scan flow?",
> "what writes into `session_ai_audit`?" — and get answers grounded in the actual code
> **and** the intent docs, with file/line citations.

## What's in here

```
README.md                    this file
RUNBOOK.md                   step-by-step: build the graph, serve it, deploy it
Dockerfile                   packages graph.json behind Streamable HTTP MCP on :8080
docker-compose.yml           one-command local/prod serving
docs/mcp-architecture.html   visual doc: architecture diagram, MCP tools, query flow
scripts/build-graph.sh       assembles a clean build-root of all 4 repos' source + docs
.graphifyignore              what to exclude from the graph (deps, binaries, models)
graphify-out/                the built graph: graph.json (served), graph.html (viz),
                             GRAPH_REPORT.md (god nodes, audit trail), cache/ (rebuild cache)
mcp/claude-code-mcp.md       per-assistant wiring + query patterns & known corpus issues
```

## The four repos it graphs

| Repo | Role |
|---|---|
| `kiotel_hardware` | the physical lobby kiosk (Windows .NET, WebView2 shell) |
| `kiotel_web` | **the platform hub** — dashboards + real-time backend (Express/Knex/Socket.IO/Next.js) |
| `audio_services` | records + AI-audits each check-in call (FastAPI, Deepgram, LLM audit) |
| `kiotel-pms` | property management (rooms, rates, bookings) |

Each repo carries `README.md` + `docs/{SYSTEM,ARCHITECTURE,DATA_MODEL,INTEGRATIONS}.md`.
Graphify's structural pass (tree-sitter AST) discovers *what connects to what*; the docs
supply *why*, including cross-repo intent (kiosk↔platform, platform↔audit, platform↔pms).
On top of that, this build adds verified concept→code bridge edges so doc concepts land on
the real symbols they describe.

## Use it (no build required)

```bash
git clone https://github.com/nxgp/kiotel-knowledge-graph && cd kiotel-knowledge-graph
docker compose up -d        # Streamable HTTP MCP at http://localhost:8080/mcp
```

Claude Code config:

```json
{
  "mcpServers": {
    "kiotel-graph": { "type": "http", "url": "http://localhost:8080/mcp" }
  }
}
```

10 MCP tools are exposed: `query_graph`, `get_node`, `get_neighbors`, `get_community`,
`god_nodes`, `graph_stats`, `shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs`.
Read [`mcp/claude-code-mcp.md`](mcp/claude-code-mcp.md) for **query patterns & limits**
(fuzzy `label` params, token budgets, hop caps) and the **known corpus issues** list.
Open [`docs/mcp-architecture.html`](docs/mcp-architecture.html) for the visual overview.

Without Docker: `pip install "graphifyy[mcp]" && python -m graphify.serve graphify-out/graph.json`
(stdio for local Claude Code, `--transport http` for shared).

## Rebuild / refresh the graph

Full steps in [`RUNBOOK.md`](RUNBOOK.md). Short version:

```bash
./scripts/build-graph.sh                 # assemble build-root/ from the 4 repos
/graphify ./build-root --mode deep       # in Claude Code (or headless with an API key)
rm -rf graphify-out && mv build-root/graphify-out ./graphify-out
docker build -t kiotel-graph-mcp .       # repackage the new graph
```

`--update` re-extracts only changed files on subsequent runs. After a rebuild, re-apply the
gap-repair pass (dangling internal imports + concept→code bridges — see RUNBOOK notes and
the commit history) before publishing, and rebuild the Docker image.

## Production

Set `GRAPHIFY_API_KEY` on the container, put TLS in front, point clients at
`https://<host>/mcp`. The image is stateless with a TCP healthcheck — safe behind a load
balancer. Registry push commands are in the RUNBOOK.
