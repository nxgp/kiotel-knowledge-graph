# Runbook — build the Kiotel knowledge graph & serve it as MCP

This produces one combined Graphify knowledge graph across all **19 kiotel-team repos** and
wires it up as an MCP server the whole dev team can use. You run this locally (it uses your
machine's AI assistant / API key for the semantic pass). ~1–2 h the first time (4.8k files).

## Prerequisites

- **Python 3.10+**, and ideally [`uv`](https://docs.astral.sh/uv/) or `pipx`. Works on
  Windows (no `rsync` needed any more) as well as macOS/Linux.
- **All 19 kiotel-team repos cloned side by side** in one folder (`REPOS_DIR`, default
  `../kiotel`), each under its GitHub name — see the table in `README.md`.
  `git pull` each one first so the graph reflects `main`.
- **Docs already in each repo** — done: every repo has `README.md` + `docs/` (SYSTEM,
  ARCHITECTURE, DATA_MODEL/DEVICES, INTEGRATIONS). These are the semantic layer.
- For the semantic pass, either **Claude Code installed** (recommended — uses your existing
  model, no extra key) *or* an `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`.

## Step 1 — Install Graphify

```bash
uv tool install graphifyy      # recommended
# or: pipx install graphifyy
# or: pip install graphifyy

graphify install               # one-time: registers the /graphify skill with Claude Code / Cursor / Codex
pip install "graphifyy[sql,terraform]==0.9.25" networkx   # SQL/HCL parsers (150+ .sql files are
                                                          # silently skipped without them) + verify-graph.py
```

> Note the double-y package name: `graphifyy` (CLI command is `graphify`).

## Step 2 — Assemble the build root

From this repo:

```bash
./scripts/build-graph.sh
# point at your clones if they are not in ../kiotel:
# REPOS_DIR=~/code/kiotel-team ./scripts/build-graph.sh
# ./scripts/build-graph.sh --dry-run     # per-repo file/MB counts, copies nothing
```

This copies the **source + docs** of all 19 repos into `./build-root/<repo-name>/` (via
`scripts/assemble-build-root.py`), excluding deps, build output, binaries, models, secrets
(`.env`, `api_key*`, keys, Postman environments — `*.example` templates are kept) and any
file over 1 MB. Two repos are special-cased (see the `REPOS` table in the script):

- `kiosk_dashboard_windows_client` is a ClickOnce publish dump (~8k files, ~700 MB). Only
  `docs/`, `multi-L-TTS/`, the release scripts and `Application Files/core_kiosk/kiosk_Source`
  are copied (to `kiosk_Source/`).
- `kiotel_dev_docs` is 3,400+ Bruno YAML request files. They are condensed to one
  `<service>.md` API catalog per collection (`METHOD url — name`, grouped by folder).

It also drops `.graphifyignore` in place. **Adding a repo = one line in `REPOS`**, one in
`verify-graph.py`'s `REPOS`, then rerun.

## Step 3 — Build the graph

**Path A — with Claude Code (recommended, no API key juggling):**

```
/graphify ./build-root --mode deep
```
(Add `--no-gitignore` if the graph repo's own `.gitignore` — which lists `build-root/` —
makes Graphify find 0 files.)

Run that inside Claude Code. `--mode deep` extracts richer relationships; drop it for a
faster first pass. Code parsing is local (tree-sitter); the docs/prose extraction uses your
Claude model.

**Path B — headless (CI or no Claude Code):**

```bash
export ANTHROPIC_API_KEY=sk-...        # or OPENAI_API_KEY / GEMINI_API_KEY
cd build-root && graphify extract . --backend claude --mode deep --no-gitignore
# no API key but Claude Code installed (Pro/Max plan)?  --backend claude-cli
# (needs `claude` on PATH; the VS Code extension bundles claude.exe under
#  ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/)
```
`--no-gitignore` is required: `build-root/` is git-ignored in this repo and Graphify honours
that, which yields "found 0 code, 0 docs".

Either way, output lands in `build-root/graphify-out/`:
- `graph.json` — the queryable graph (this is the artifact the MCP serves)
- `graph.html` — interactive visualization (open it to eyeball the clusters)
- `GRAPH_REPORT.md` — key concepts, "god nodes", and suggested questions

## Step 4 — Publish the graph into this repo

```bash
rm -rf graphify-out && mv build-root/graphify-out ./graphify-out
# ALWAYS re-apply, in this order (schema extractors create nodes that repair-bridges target):
python scripts/extract-db-schema-nodes.py graphify-out/graph.json build-root  # kiosk_dashboard DB tables (db_schema_view.ts + migrations)
python scripts/extract-prisma-nodes.py    graphify-out/graph.json build-root  # kiotel-space + kiotel_admin_central Prisma models/enums
python scripts/apply-graph-repairs.py     graphify-out/graph.json             # curated repairs + concept->code + cross-repo bridges
python scripts/verify-graph.py            graphify-out/graph.json build-root  # PASS gate: coverage, connectivity, dangling, bridges, spot-check
git add graphify-out README.md RUNBOOK.md scripts .graphifyignore mcp
git commit -m "Kiotel combined knowledge graph"
git push
```

Open `graphify-out/graph.html` and skim `GRAPH_REPORT.md` — you should see one cluster per
repo plus cross-repo edges (kiosk↔dashboard, dashboard↔AI audit, space↔admin, payments↔PMS
aggregator…) from the curated bridges and the `docs/` of each repo.

## Step 5 — Serve it as MCP

```bash
python -m graphify.serve graphify-out/graph.json                    # stdio (for Claude Code)
# or HTTP:
python -m graphify.serve graphify-out/graph.json --transport http --port 8080
```

Then add it to each dev's assistant — see [`mcp/claude-code-mcp.md`](mcp/claude-code-mcp.md).

## Step 6 — Docker (shared/production deployment)

The repo ships a `Dockerfile` + `docker-compose.yml` that package the committed
`graphify-out/graph.json` behind the Streamable HTTP MCP transport:

```bash
docker build -t kiotel-graph-mcp .
docker run -d -p 8080:8080 --name kiotel-graph kiotel-graph-mcp
# or: docker compose up -d
curl -s http://localhost:8080/mcp   # MCP endpoint (Streamable HTTP)
```

To require auth, set `GRAPHIFY_API_KEY` in the container environment and give clients the
same key. The image is stateless (`--stateless`), so it can sit behind a load balancer and
be restarted freely. Rebuild the image whenever `graphify-out/graph.json` changes.

**Pushing to a registry** (adjust org/registry to yours; GHCR needs a classic PAT with
`write:packages`):

```bash
docker tag kiotel-graph-mcp ghcr.io/<org>/kiotel-graph-mcp:latest
echo $GHCR_PAT | docker login ghcr.io -u <user> --password-stdin
docker push ghcr.io/<org>/kiotel-graph-mcp:latest
```

Production checklist: set `GRAPHIFY_API_KEY`, put TLS in front (the server speaks plain
HTTP), and point clients at `https://<host>/mcp`. The image has a TCP healthcheck on :8080.

## Refreshing the graph later

Re-run steps 2–4. Graphify supports `--update` to re-extract only changed files, so
subsequent builds are fast. Consider a CI job (there's a community `code-review-graph`
action) that rebuilds on merge to keep the graph current.

## Troubleshooting

- **Build root is huge / slow** → run `./scripts/build-graph.sh --dry-run`; the Windows
  client must contribute ~175 files (not ~8,000). If a `.dll`/`.onnx`/`.resx` slipped in,
  add it to `COMMON_EXCLUDE_GLOBS` in `assemble-build-root.py` and to `.graphifyignore`.
- **"found 0 code, 0 docs"** → pass `--no-gitignore` (see step 3).
- **"N .sql file(s) contributed nothing"** → `pip install "graphifyy[sql,terraform]"`.
- **Graph has fewer nodes than a previous run** → Graphify refuses to overwrite by default;
  add `--force` only when you intend to shrink it.
- **No cross-repo edges** → make sure `docs/INTEGRATIONS.md` and `docs/SYSTEM.md` made it
  into the build root (they carry the cross-repo intent).
