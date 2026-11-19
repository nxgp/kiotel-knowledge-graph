#!/usr/bin/env bash
#
# build-graph.sh — assemble a clean "build root" containing the source + docs of all 19
# kiotel-team repos, so Graphify can build ONE combined knowledge graph across the estate.
#
# It does NOT run Graphify (that's your step — see RUNBOOK.md), because Graphify's semantic
# pass uses your local AI assistant / API key. This script only does the deterministic part:
# copy the right files, exclude the junk, and drop the .graphifyignore in place.
#
# The copy logic lives in scripts/assemble-build-root.py (cross-platform; the old rsync loop
# did not run on Windows and hard-coded pre-rename snapshot folders). Per-repo include/exclude
# rules are in the REPOS table at the top of that file.
#
# Usage:
#   REPOS_DIR=/path/to/folder/holding/all/19/clones ./scripts/build-graph.sh
#   ./scripts/build-graph.sh --dry-run        # report counts only, copy nothing
#   ./scripts/build-graph.sh kiotel_ui_kit    # (re)assemble a single repo's files
#
# REPOS_DIR defaults to ../kiotel (a sibling of this repo). Each repo must be cloned there
# under its kiotel-team name, e.g. $REPOS_DIR/kiosk_dashboard, $REPOS_DIR/kiotel-space.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then
      PY="$c"; break
    fi
  done
fi
[ -n "$PY" ] || { echo "ERROR: Python 3.10+ not found (set PYTHON=...)" >&2; exit 1; }

"$PY" "$HERE/scripts/assemble-build-root.py" "$@"

case " $* " in *" --dry-run "*) exit 0;; esac

echo
echo "Next: build the graph (see RUNBOOK.md). Quickest path if you use Claude Code:"
echo "    graphify install         # one-time, registers the /graphify skill"
echo "    /graphify $HERE/build-root --mode deep"
echo "  or headless:  graphify extract build-root --backend claude-cli --mode deep"
echo
echo "Then move build-root/graphify-out/ up to ./graphify-out/ and run the post-build"
echo "scripts listed in RUNBOOK.md step 4."
