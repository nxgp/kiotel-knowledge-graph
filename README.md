# kiotel-graph-mcp

A **combined knowledge graph of the entire Kiotel codebase** — built with
[Graphify](https://github.com/Graphify-Labs/graphify) across all four repos — served as an
**MCP server** so any dev's AI assistant (Claude Code, Cursor, Codex…) can query how the
Kiotel system fits together and *why*.

> **What this gives your team:** ask "what calls the recording service?", "where does a
> guest's ID data flow?", "what connects the kiosk to the platform?", or "why does the audit
> service read property rules from the platform?" — and get answers grounded in the actual
> code **and** the intent docs, without any one dev holding the whole system in their head.

## What's in here

```
README.md               this file
RUNBOOK.md              step-by-step: build the graph and serve it as MCP
scripts/build-graph.sh  assembles a clean "build root" of all 4 repos' source + docs
.graphifyignore         what to exclude from the graph (deps, binaries, models)
graphify-out/           ← the built graph lands here (graph.json, graph.html, GRAPH_REPORT.md)
mcp/claude-code-mcp.md  how each dev wires the graph into their assistant
```

## The four repos it graphs

| Repo | Role |
|---|---|
| `kiotel_hardware` | the physical lobby kiosk (Windows .NET) |
| `kiotel_web` | **the platform hub** — dashboards + real-time backend |
| `audio_services` | records + AI-audits each check-in call |
| `kiotel-pms` | property management (rooms, rates, bookings) |

Each repo now carries `README.md` + `docs/{SYSTEM,ARCHITECTURE,DATA_MODEL,INTEGRATIONS}.md`.
Those docs are the **semantic layer** — Graphify's structural analysis discovers *what
connects to what*; the docs tell it *why those connections are correct*. The combined graph
therefore captures both the wiring and the intent, including the cross-repo edges
(kiosk↔platform, platform↔audit, platform↔pms).

## Quick start

```bash
./scripts/build-graph.sh          # assemble build-root/ from the 4 repos
graphify install                  # one-time
/graphify ./build-root --mode deep   # (in Claude Code) build the graph
mv build-root/graphify-out ./graphify-out
python -m graphify.serve graphify-out/graph.json   # serve as MCP
```

Full detail in [`RUNBOOK.md`](RUNBOOK.md). Wiring per assistant in
[`mcp/claude-code-mcp.md`](mcp/claude-code-mcp.md).

## Keeping it current

Re-run the build after significant changes (`graphify ... --update` re-extracts only what
changed). A CI job on merge keeps the graph — and therefore the team's shared understanding —
from drifting.
