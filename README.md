# kiotel-knowledge-graph

A **combined knowledge graph of the entire Kiotel codebase** — built with
[Graphify](https://github.com/Graphify-Labs/graphify) across all **20 kiotel-team repos** — served as a
**dockerized MCP server** so any dev's AI assistant (Claude Code, Cursor, Codex…) can query
how the whole Kiotel estate fits together and *why*.

**Current graph** (rebuilt 2026-11-20 across all 20 repos): **37,524 nodes · 94,800 edges ·
1,716 communities**, with all 20 repos in one connected component. Provenance on every edge:
97% EXTRACTED (structural fact), 3% INFERRED (including the curated concept→code and
cross-repo bridges), 0% unresolved AMBIGUOUS. Coverage: 98.8% of the 4,729-file corpus (the
gaps are JSON data/fixtures and a few docs with no extractable structure; `verify-graph.py`
lists them). Verifier result: **PASS**.

> The previous graph (2026-08-16) covered 14 local snapshots under pre-rename names
> (`kiotel_web`, `kiotel_hardware`, `audio_services`…). [`docs/kiotel-estate.html`](docs/kiotel-estate.html)
> and the other HTML docs are still **that** 14-repo analysis (what each repo does, how they
> connect, the security findings) and use the old names — see the rename table below.

> Ask "what connects the kiosk to the platform?", "where does a guest's ID scan flow?",
> "what writes into `session_ai_audit`?", "how does payments post a charge to the PMS?" — and
> get answers grounded in the actual code **and** the intent docs, with file/line citations.

## What's in here

```
README.md                    this file
RUNBOOK.md                   step-by-step: build the graph, serve it, deploy it
Dockerfile                   packages graph.json behind Streamable HTTP MCP on :8080
docker-compose.yml           one-command local/prod serving
docs/mcp-architecture.html   visual doc: architecture diagram, MCP tools, query flow
scripts/build-graph.sh       entry point: assembles the build root of all 20 repos' source + docs
scripts/assemble-build-root.py   the cross-platform copier (per-repo rules live in its REPOS table)
scripts/apply-graph-repairs.py   post-build repairs + applies both bridge files
scripts/bridges.json         concept→code bridges (doc/concept node → the code it describes)
scripts/cross-repo-bridges.json  cross-repo bridges, each with file+line evidence
scripts/extract-db-schema-nodes.py / extract-prisma-nodes.py   DB table / Prisma model nodes
scripts/verify-graph.py      PASS/FAIL gate: coverage, per-repo counts, dangling, bridges, spot-check, graph.json size guard
scripts/smoke-test-mcp.py    real-query smoke test of a running MCP server (HTTP endpoints, 10 tools, cross-repo questions)
scripts/export-graph-html.py aggregated community view -> graphify-out/graph.html (graph is >5k nodes)
scripts/write-build-metadata.py  file-sizes.json + cost.json for the metrics dashboard
scripts/nightly-update.py     nightly incremental update: detect changed repos, re-extract only what changed, verify, publish
scripts/nightly-config.json  org + the branch each repo is graphed from
.github/workflows/nightly-graph-update.yml   runs the above every night (see RUNBOOK 'Nightly update')
.graphifyignore              what to exclude from the graph (deps, binaries, models, secrets)
graphify-out/                the built graph: graph.json (served), graph.html (viz),
                             GRAPH_REPORT.md (god nodes, audit trail), manifest.json, cost.json
mcp/claude-code-mcp.md       per-assistant wiring + query patterns & known corpus issues
```

## The 20 repos it graphs

Each repo is graphed under its **current kiotel-team name**; `source_file` paths and node ids
start with that name.

**The kiosk platform** (the revenue product):
| Repo | Role | Was in the Aug graph as |
|---|---|---|
| `kiosk_dashboard` | **the hub** — Next.js + Express/Socket.IO backend; owns the `kiosk` DB, drives kiosks | `kiotel_web` |
| `kiosk_dashboard_windows_client` | the lobby kiosk app (.NET + WebView2); only `kiosk_Source/`, `docs/`, `multi-L-TTS/`, release scripts are graphed | `kiotel_hardware` |
| `kiosk_pms_autofill_extension` | Chrome MV3 extension; OAuth2 PKCE, autofills guest data into PMS screens | `kiotel_pms_autofill` |
| `kiosk_ai_services` | records + AI-audits each call (FastAPI, Deepgram/RunPod, LLM audit → `session_ai_audit`); now also holds the STT/translation prototypes | `audio_services`, `stt_tts_inhouse`, `guest_translation` |
| `kiotel_transcription_service` | RunPod serverless WhisperX worker (transcription + diarization) | `speech_to_text_serverless` |
| `kiotel_vision_ai_services` | **new** — RunPod serverless vision worker: analyses a recorded front-desk video and returns a plain-English summary; extracted from `kiosk_ai_services`' old `Vision_audit/` folder, which still calls it | — |
| `kiosk_ai_avatar` | **new** — standalone LLM + TTS + MuseTalk lip-sync avatar prototype | — |

**Identity, workspace & internal suite:**
| Repo | Role | Was |
|---|---|---|
| `kiotel_admin_central` | identity provider and roles for everything (EdDSA JWTs, JWKS, MFA, RBAC) | `kiotel_admin` |
| `kiotel-space` | staff/customer workspace — chat, tasks, calls UI; delegates auth to admin | `kiotel_space` |
| `kiotel_communications_services` | **new** — LiveKit call service behind Space's calls | — |
| `kiotel_internal_operations_portal` / `_backend` | ops + HR portal and its API | `Kiotel_portal_front` / `hr_kiotel_backend` |
| `kiotel_ui_kit` | **new** — shared React components (`@kiotel-team/ui-kit`) | — |

**Integrations & property systems:**
| Repo | Role | Was |
|---|---|---|
| `kiotel_pms_aggregator_service` | **new** — one API over the PMS adapters (SynXis first; Cloudbeds/ASI/Mews/Apaleo) | — |
| `kiotel_payment_aggregator_service` | **new** — payments ledger + QuickBooks sync | — |
| `kiotel_channel_manager` | **new** — OTA sync skeleton (Fastify + BullMQ + Drizzle) | — |
| `kiotel-pms` | Kiotel's own PMS (rooms/rates/reservations/folio) | `kiotel-pms` (stale → refreshed) |
| `kiotel_lox` | door-key encoding — Go API + .NET agent | `kiotel_lox` (stale → refreshed) |
| `kiotel_chatbot_service` | LangGraph support bot; NL→SQL over the platform DB | `chatbot` |
| `kiotel_dev_docs` | **new** — Bruno API collections, condensed to one API-catalog per service | — |

`kiotel_dev_docs` is 3,400+ Bruno YAML request files; the build condenses each collection into
one `METHOD url — name` markdown catalog, which is linked to the service's entry point.

Graphify's structural pass (tree-sitter AST, plus SQL/HCL parsers) discovers *what connects to
what*; the docs supply *why*. On top of that this repo adds **bridges** — curated edges that
join the repos into one queryable component:

- `scripts/cross-repo-bridges.json` — **57** cross-repo edges. 15 carried over from the
  2026-08 deep-read (re-keyed to current names, all still resolve), 7 new code-level ones
  grounded in file+line evidence (payments → PMS aggregator folio API, Space → communications
  call-token contract, Space → UI kit, AI services → transcription RunPod worker, AI services →
  vision RunPod worker (2 edges), kiosk ↔ avatar),
  and the API-catalog links for `kiotel_dev_docs`.
- `scripts/bridges.json` — **47** concept→code edges, re-anchored onto the current doc/concept
  nodes. 47 of the original 94 had no counterpart in the rewritten docs (or the code they pointed to was
  deleted) and were dropped, not guessed.

## Nightly updates

A GitHub Actions workflow keeps the graph current: each night it compares the latest commit of
every repo with `graphify-out/source-state.json`, stops if nothing moved, and otherwise
re-extracts only what changed (Graphify's content-keyed semantic cache means only new or edited
docs reach the AI), verifies the result and opens/updates a PR on `graph-nightly`. It never
pushes to the default branch. Setup (two secrets, one repo setting) and the run commands are in
[`RUNBOOK.md`](RUNBOOK.md#nightly-update-github-actions).

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

Without Docker: `pip install "graphifyy[mcp]==0.9.25" "mcp<2" && python -m graphify.serve graphify-out/graph.json`
(stdio for local Claude Code, `--transport http` for shared).

## Rebuild / refresh the graph

Full steps in [`RUNBOOK.md`](RUNBOOK.md). Short version (all 20 repos cloned side by side):

```bash
REPOS_DIR=/path/to/clones ./scripts/build-graph.sh      # assemble build-root/ (add --dry-run to preview)
cd build-root && graphify extract . --backend claude --mode deep --no-gitignore && cd ..
rm -rf graphify-out && mv build-root/graphify-out ./graphify-out
python scripts/extract-db-schema-nodes.py graphify-out/graph.json build-root
python scripts/extract-prisma-nodes.py    graphify-out/graph.json build-root
python scripts/apply-graph-repairs.py     graphify-out/graph.json
graphify cluster-only build-root --graph graphify-out/graph.json --no-viz
python scripts/export-graph-html.py       graphify-out/graph.json
python scripts/write-build-metadata.py build-root graphify-out --input-tokens N --output-tokens M
python scripts/verify-graph.py            graphify-out/graph.json build-root   # must PASS
# start mcp/metrics_server.py (RUNBOOK 'Smoke-test the MCP server'), then:
python scripts/smoke-test-mcp.py http://localhost:8081
docker build -t kiotel-graph-mcp .
```

Adding a repo to the graph = one entry in `REPOS` in `assemble-build-root.py` and one in
`verify-graph.py`. `--update` re-extracts only changed files on subsequent runs. After any
rebuild, **always re-run `apply-graph-repairs.py`** — it re-applies the curated gap repairs
and both bridge files (idempotent) — then rebuild the Docker image.

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
    community view (1,716 communities), since the full 37k-node graph is too dense to
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
