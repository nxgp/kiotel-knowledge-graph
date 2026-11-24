# Runbook — build the Kiotel knowledge graph & serve it as MCP

This produces one combined Graphify knowledge graph across all **20 kiotel-team repos** and
wires it up as an MCP server the whole dev team can use. You run this locally (it uses your
machine's AI assistant / API key for the semantic pass). ~1–2 h the first time (4.8k files).

## Prerequisites

- **Python 3.10+**, and ideally [`uv`](https://docs.astral.sh/uv/) or `pipx`. Works on
  Windows (no `rsync` needed any more) as well as macOS/Linux.
- **All 20 kiotel-team repos cloned side by side** in one folder (`REPOS_DIR`, default
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

This copies the **source + docs** of all 20 repos into `./build-root/<repo-name>/` (via
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

## Smoke-test the MCP server before merging / deploying

Start the server exactly as production does (`mcp/metrics_server.py`), then run the smoke test.
No Docker needed; with Docker, `docker compose up -d` serves the same code on :8080.

```powershell
# PowerShell (adjust the python path); bash users: export the same variables
$env:GRAPH_PATH="graphify-out\graph.json"; $env:FILE_SIZES="graphify-out\file-sizes.json"
$env:CORPUS_MANIFEST="graphify-out\manifest.json"; $env:BUILD_COST_JSON="graphify-out\cost.json"
$env:DASHBOARD_HTML="mcp\dashboard.html"; $env:GRAPH_VIZ_HTML="graphify-out\graph.html"
$env:METRICS_DB="$env:TEMP\metrics.db"; $env:PORT="8081"
python mcp\metrics_server.py            # leave running

python scripts/smoke-test-mcp.py http://localhost:8081   # in another terminal; exits non-zero on failure
```

It checks `/healthz`, `/stats`, `/dashboard`, `/graph`, that all 10 MCP tools exist, and that real
cross-repo questions come back citing the right repos/files (payments → PMS aggregator, Space call
token → communications gateway, AI services → transcription worker, Space → UI kit,
`session_ai_audit`, kiosk ↔ platform). Point it at the deployed URL (`--api-key`) after a deploy.

## Large graph.json (Git LFS evaluation, 2026-11-20)

`graphify-out/graph.json` is committed as a plain file and is the largest artifact in the repo.

| Fact | Value |
|---|---|
| Size now | ~50.3 MB raw (already past GitHub's ~50 MB warning line); ~2.1 MB gzip-compressed |
| `.git` size with 7 historic versions | 5.5 MB (git stores it compressed), so each rebuild adds roughly 2 MB |
| GitHub limits | regular files over 100 MB are rejected (a warning appears around 50 MB: commonly documented, confirm in [GitHub's docs](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)); LFS allows files up to 2 GB on Free/Pro |
| LFS cost | storage + bandwidth quotas apply and are billed past the included amount ([GitHub LFS billing](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)); older docs quote 1 GB, current docs 10 GiB on Free, so confirm for the `nxgp` org |
| How production is deployed | `scripts/deploy-do.sh` builds the image **locally** and pushes it to DOCR; App Platform never clones this repo, so LFS does not affect the live deploy |

**Recommendation: do not enable LFS yet.** The file is under the 100 MB limit and its history cost
is small, but it has crossed ~50 MB, so plan option 1 below before it nears 100 MB. LFS would also break the "no build required" path in the README: `git clone` without
git-lfs installed gives a ~130-byte pointer file in place of `graph.json`, and `docker compose
up` would then build an image that fails at startup. Every consumer, CI job and the deploy
machine would need `git lfs install` + `git lfs pull`.

`verify-graph.py` now has a size guard: it prints a warning at 45 MB and **fails the run at
95 MB**, so the switch is planned rather than discovered by a rejected push. If it trips, in
order of preference:

1. **Commit a compressed artifact**: store `graph.json.gz` (~2 MB) and `gunzip` it in the
   Dockerfile (`RUN gunzip -k graph.json.gz` before use). No extra tooling for consumers.
2. **Git LFS**: `git lfs track "graphify-out/graph.json"`, commit `.gitattributes`, and update the
   README/Dockerfile/CI to run `git lfs pull`. Do **not** run `git lfs migrate import` on a shared
   branch (it rewrites history); that is Ravi's call.
3. **Stop committing it**: publish `graph.json` as a release asset or only in the DOCR image.

## Nightly update (GitHub Actions)

`.github/workflows/nightly-graph-update.yml` runs `scripts/nightly-update.py` every night
(20:30 UTC) so the graph follows the code. It never pushes to the default branch: it commits
to `graph-nightly` and opens/updates a pull request.

**What "only update what changed" means**
1. *Repo level*: it reads the latest commit of every repo (`git ls-remote`) and compares with
   `graphify-out/source-state.json`, the commits the published graph was built from. If nothing
   moved it stops: no clone, no AI, no PR.
2. *File level*: Graphify's semantic cache is keyed by file content, so only new or edited
   docs/configs reach the AI; everything else is a cache hit. Code files are parsed locally and
   cost no tokens. The semantic cache (`graphify-out/cache/semantic-deep`, ~0.7 MB of extracted
   nodes/edges, no document text) is **committed to the repo as the seed**, so a fresh checkout
   starts incrementally; the workflow also carries it with `actions/cache`, and saves it even
   when a run fails, so tokens already spent are never spent twice. The AST cache is
   deliberately **not** reused or committed: it stores the absolute build path inside node ids,
   so a cache made under another path renames nodes and breaks bridges (seen in testing).
   Measured: one edited README = 1 cache miss = ~87k tokens; a full rebuild is ~5.1M.

**Steps of a run**: shallow-clone all repos (the Windows client with a sparse checkout) →
assemble build-root → incremental `graphify extract` → DB/Prisma extractors → repairs and
bridges → cluster + label → graph.html → metadata → `verify-graph.py` → MCP smoke test →
publish. A failing stage stops the run *before* publish, so a bad build never replaces the
good graph. The log prints only repo names and counts (this repo is public, the code is not).

**Setup an admin must do once**
- Secret `KIOTEL_REPOS_TOKEN`: fine-grained PAT or app token with read access to the
  `kiotel-team` repos (all 20). The graph repo is in `nxgp`, so `GITHUB_TOKEN` cannot read them.
- Secret `ANTHROPIC_API_KEY`: the semantic pass in CI (the Claude Code login is not available).
  Default model `claude-sonnet-4-6`, Graphify's built-in price is $3 / $15 per 1M tokens.
  Rough cost: nothing on a night with no commits; about $1 for a few changed doc sets;
  about $20 for a full rebuild (5.1M in / 0.4M out). Run-to-run cost scales with doc/config churn.
- Settings > Actions > General > "Allow GitHub Actions to create and approve pull requests".
- **First run**: nothing special. The committed seed cache means the first night is incremental
  too. If the seed is ever deleted *and* `actions/cache` has expired, the guard refuses a full
  AI pass (about 5M tokens, ~$20); re-run once with `allow_full_rebuild` ticked if that cost is
  intended.

**Changed-docs detection and the PR description**: `graphify-out/source-state.json` stores a
manifest of every doc/image Graphify sends to the AI (path + content hash, the same files
Graphify classifies as documents) next to the commit of every repo. A run diffs it against the
freshly assembled build, so it knows exactly which docs were added, modified or removed since the
published graph, and writes `pr-body.md` for the nightly PR: repos with new commits (old -> new
commit), the changed docs per repo (capped), graph size before/after and how many files the AI
actually re-read. Note: this lists doc paths of private repos in a public repo's PR (graph.json
already contains the same paths); say so if that is not acceptable and the list can be reduced
to counts. Refresh the manifest of an existing state file with
`python scripts/nightly-update.py init-docs` (it must describe the build the published graph came from).

**Secrets are placeholders until added**: the first workflow step stops with an error that names
the missing secret and where to add it (`KIOTEL_REPOS_TOKEN` always; `ANTHROPIC_API_KEY` for
`mode=full`). If the PR permission is off, the publish step pushes `graph-nightly`, then fails
with a message naming the setting to enable.

**Validate locally with your own read-only token** (no AI key needed for check mode):
1. GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens >
   Generate new token. Resource owner `kiotel-team` (an org owner may have to approve it),
   repository access to the 20 repos, permission Contents: Read-only, a short expiry.
2. In your terminal only (never in a file, commit or chat):
   ```powershell
   $env:KIOTEL_REPOS_TOKEN = "<paste token>"
   python scripts/nightly-update.py run --source remote --mode check --work-dir ./nightly-work
   ```
   The script passes the token to git through git's environment config (not argv, not a file,
   never printed). Check mode clones all repos, assembles the build, lists the changed docs and
   writes `nightly-work/pr-body.md`. Drop `--mode check` (and add `--backend claude-cli` to use
   your Claude Code login) for the full update; add `--publish --graph-out <scratch copy>` to
   rehearse the publish without touching the repo's `graphify-out`.

**Branches**: `scripts/nightly-config.json` lists the branch each repo is graphed from
(default `main`). Needs the lead's confirmation: `kiosk_dashboard_windows_client` (`main` is
ahead of its default `development`) and `kiotel_communications_services` (`main` and `master`
diverged; `master` has the recent work).

**Run it by hand** (Actions > Run workflow): `mode=check` clones and assembles only (no AI, use
it to prove the token works), `mode=full publish=false` builds and verifies without committing,
`publish=true` also opens the PR, `force` rebuilds even if nothing changed.
A push to the PR branch that touches the workflow/script also runs `check` mode; remove that
`push:` trigger once the workflow is merged if you do not want it.

**Run it locally** (uses existing clones and your Claude Code login):
```bash
python scripts/nightly-update.py init-state --repos-dir ../kiotel      # baseline (already committed)
python scripts/nightly-update.py run --source local --repos-dir ../kiotel     --backend claude-cli --work-dir ./nightly-work [--publish] [--force] [--mode check]
python scripts/nightly-update.py run --source remote --backend claude-cli   # clones from GitHub
```
`--graph-out DIR` points it at a scratch copy of `graphify-out` so you can rehearse a publish.

**If a night fails**: nothing was published. The usual cause is `verify-graph.py` reporting
broken bridges after a doc rewrite renamed an AI-named concept node; fix the bridge in
`scripts/bridges.json` / `scripts/cross-repo-bridges.json` (as in the 2026-11-20 rebuild) and re-run.

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
