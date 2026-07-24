# Runbook — build the Kiotel knowledge graph & serve it as MCP

This produces one combined Graphify knowledge graph across all four Kiotel repos and wires
it up as an MCP server the whole dev team can use. You run this locally (it uses your
machine's AI assistant / API key for the semantic pass). ~15–30 min the first time.

## Prerequisites

- **Python 3.10+**, and ideally [`uv`](https://docs.astral.sh/uv/) or `pipx`.
- The four Kiotel repos checked out locally. Defaults assume:
  - `~/kiotel_web`
  - `~/Downloads/kiotel-pms-main`
  - `~/Downloads/kiotel_dashboard_audio_services-main`
  - `~/kiotel_hardware`
  (Override with env vars — see step 2.)
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
```

> Note the double-y package name: `graphifyy` (CLI command is `graphify`).

## Step 2 — Assemble the build root

From this repo:

```bash
./scripts/build-graph.sh
# override paths if yours differ:
# WEB=~/code/kiotel_web PMS=~/code/kiotel-pms AUDIO=~/code/audio_services HW=~/code/kiotel_hardware ./scripts/build-graph.sh
```

This copies the **source + docs** of all four repos into `./build-root/`, excluding deps,
build output, binaries, and the hardware repo's vendored DLLs / OCR models (it includes only
`kiosk_Source/` + the docs). It also drops `.graphifyignore` in place.

## Step 3 — Build the graph

**Path A — with Claude Code (recommended, no API key juggling):**

```
/graphify ./build-root --mode deep
```

Run that inside Claude Code. `--mode deep` extracts richer relationships; drop it for a
faster first pass. Code parsing is local (tree-sitter); the docs/prose extraction uses your
Claude model.

**Path B — headless (CI or no Claude Code):**

```bash
export ANTHROPIC_API_KEY=sk-...        # or OPENAI_API_KEY / GEMINI_API_KEY
graphify extract ./build-root --backend anthropic
```

Either way, output lands in `build-root/graphify-out/`:
- `graph.json` — the queryable graph (this is the artifact the MCP serves)
- `graph.html` — interactive visualization (open it to eyeball the clusters)
- `GRAPH_REPORT.md` — key concepts, "god nodes", and suggested questions

## Step 4 — Publish the graph into this repo

```bash
rm -rf graphify-out && mv build-root/graphify-out ./graphify-out
python scripts/apply-graph-repairs.py graphify-out/graph.json   # ALWAYS: re-apply curated repairs
git add graphify-out README.md RUNBOOK.md scripts .graphifyignore mcp
git commit -m "Kiotel combined knowledge graph"
git push
```

Open `graphify-out/graph.html` and skim `GRAPH_REPORT.md` — you should see four clusters
(one per repo) plus cross-repo edges (kiosk↔platform, platform↔audit, platform↔pms) that the
`docs/INTEGRATIONS.md` + `SYSTEM.md` files taught it.

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

- **Build root is huge / slow** → confirm the hardware repo only contributed `kiosk_Source`
  + `docs` (check `du -sh build-root/*`); if a `.dll`/`.onnx`/`.resx` slipped in, add it to
  `.graphifyignore`.
- **Graph has fewer nodes than a previous run** → Graphify refuses to overwrite by default;
  add `--force` only when you intend to shrink it.
- **No cross-repo edges** → make sure `docs/INTEGRATIONS.md` and `docs/SYSTEM.md` made it
  into the build root (they carry the cross-repo intent).
