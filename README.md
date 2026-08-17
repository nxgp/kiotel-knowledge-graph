# kiotel-knowledge-graph

A **combined knowledge graph of the entire Kiotel codebase** — built with
[Graphify](https://github.com/Graphify-Labs/graphify) across all **14 repos** — served as a
**dockerized MCP server** so any dev's AI assistant (Claude Code, Cursor, Codex…) can query
how the whole Kiotel estate fits together and *why*.

**Current graph** (rebuilt 2026-08-16 across all 14 repos): **15,874 nodes · 38,524 edges ·
1,051 communities**, one connected component spanning every repo. Provenance on every edge:
97% EXTRACTED (structural fact), 3% INFERRED (including curated concept→code and 10
deep-read-verified cross-repo bridges), 0% unresolved AMBIGUOUS. Coverage: 99.1% of the
2,315-file corpus (the gaps are VSCode configs, JSON data files, and ML model-weight
manifests with no extractable structure). See [`docs/kiotel-estate.html`](docs/kiotel-estate.html)
for the full estate map — what each repo does, how they connect, and the security findings.

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

## The 14 repos it graphs

**The kiosk platform** (the revenue product):
| Repo | Role |
|---|---|
| `kiotel_web` | **the hub** — Express/Knex/Socket.IO backend + Next.js; owns the `kiosk` DB (61 tables), drives kiosks over Socket.IO, 5 auth principals |
| `kiotel_hardware` | the physical lobby kiosk (Windows .NET + WebView2, 86 RELAY commands) |
| `audio_services` | records + AI-audits each call (FastAPI, Deepgram, LLM audit → `session_ai_audit`) |
| `kiotel_pms_autofill` | Chrome MV3 extension; OAuth2 PKCE, autofills live guest data into PMS screens |

**The internal suite** (federated auth around a real IdP):
| Repo | Role |
|---|---|
| `kiotel_admin` | central identity provider (admin.kiotel.co) — EdDSA JWTs, JWKS, MFA, RBAC |
| `kiotel_space` | staff workspace (space.kiotel.co) — chat/tasks/updates; delegates auth to admin |
| `kiotel_lox` | door-key encoding (lox.kiotel.co) — Go API + .NET agent; best security posture |

**Property, portal & AI:**
| Repo | Role |
|---|---|
| `kiotel-pms` | standalone PMS (rooms/rates/reservations/folio) — no code link to the rest |
| `Kiotel_portal_front` / `hr_kiotel_backend` | company portal + HR/attendance (portal.kiotel.co) |
| `chatbot` | LangGraph support bot; NL→SQL over the platform DB |
| `stt_tts_inhouse` / `guest_translation` / `speech_to_text_serverless` | three in-house STT/translation services |

Graphify's structural pass (tree-sitter AST) discovers *what connects to what*; the docs
supply *why*. On top of that, this build adds **10 cross-repo bridges**
(`scripts/cross-repo-bridges.json`), each grounded in verified file+line evidence from a
15-agent code deep-read — these are what join all 14 repos into one queryable component.

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

`--update` re-extracts only changed files on subsequent runs. After a rebuild, **always run
`python scripts/apply-graph-repairs.py graphify-out/graph.json`** — it re-applies the
curated gap repairs (internal-import remaps, the concept→code bridges from
`scripts/bridges.json`, falsified-edge removals, verified upgrades; idempotent) — then
rebuild the Docker image.

## Usage metrics & dashboard

The container ships an observability layer (`mcp/metrics_server.py`) wrapped around the MCP
server — same `/mcp` protocol, plus:

- **`/dashboard`** — live usage dashboard (open access), two tabs:
  - **Usage**: tool calls total/today, client sessions, avg calls/session, estimated
    tokens saved, p50/p95 latency, corpus coverage (distinct files reached by real
    queries, out of the indexed total), errors, calls by tool, calls per day, top
    questions asked, most-cited source files, a build-cost-vs-ongoing-savings breakdown,
    clients, and a recent-activity feed. Auto-refreshes every 10s.
  - **Graph**: the actual node/edge visualization — graphify's own interactive
    `graph export html` output, embedded live (lazy-loaded on first click). Aggregated
    community view (411 communities), since the full 7.8k-node graph is too dense to
    render node-by-node in a browser; search, click-to-inspect, and the community
    checklist all work exactly as they do when opening `graphify-out/graph.html` directly.
- **`/graph`** — that same visualization, servable/linkable on its own.
- **`/stats`** — the JSON behind the dashboard (feed it to anything else).
- **`/healthz`** — liveness probe.

Every MCP call is recorded (SQLite; `./metrics-data/` via compose, so it survives
restarts): tool, argument, latency, response size, ok/error, client, and which source
files the answer cited. **Token savings** are an estimate: each answer cites the source
files it drew from — savings = (size of those files − answer size) ÷ 4, i.e. what an
assistant would have had to read without the graph. **No $ figure is computed anywhere**
— converting tokens to dollars requires a price-per-token assumption this server has no
basis to assert, so it's left as tokens for Kiotel to convert using whatever rate is
actually relevant. The one-time **graph build cost**, by contrast, is not an estimate —
it's the real input/output token count from the actual extraction run, read from
`graphify-out/cost.json` (baked into the image), shown alongside cumulative savings for a
rough build-vs-payoff picture — both figures clearly labeled as real vs. estimated so
they're never confused.

**On "which model is asking":** MCP's `initialize` handshake only exposes the connecting
*tool's* name/version (e.g. `claude-code/2.1`) — the protocol has no field for the
underlying LLM. The dashboard's Clients panel is labelled accordingly; there's no
fabricated model-attribution column.

Note: App Platform's disk is ephemeral — metrics reset on redeploy there; use a droplet
volume for long-lived history. The metrics DB only ever reflects real MCP traffic — it
starts empty on every fresh deploy and is never pre-seeded.

## Production

Set `GRAPHIFY_API_KEY` on the container, put TLS in front, point clients at
`https://<host>/mcp`. The image is stateless with a TCP healthcheck — safe behind a load
balancer. Registry push commands are in the RUNBOOK.
