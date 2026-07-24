#!/usr/bin/env bash
#
# build-graph.sh — assemble a clean "build root" containing the source + docs of all four
# Kiotel repos, so Graphify can build ONE combined knowledge graph across the whole system.
#
# It does NOT run Graphify (that's your step — see RUNBOOK.md), because Graphify's semantic
# pass uses your local AI assistant / API key. This script only does the deterministic part:
# copy the right files, exclude the junk, and drop the .graphifyignore in place.
#
# Usage:
#   ./scripts/build-graph.sh
# Override repo locations if yours differ:
#   WEB=/path AUDIO=/path PMS=/path HW=/path ./scripts/build-graph.sh
#
set -euo pipefail

# ---- repo locations (defaults match the machine these docs were prepared on) ----
WEB="${WEB:-$HOME/kiotel_web}"
PMS="${PMS:-$HOME/Downloads/kiotel-pms-main}"
AUDIO="${AUDIO:-$HOME/Downloads/kiotel_dashboard_audio_services-main}"
HW="${HW:-$HOME/kiotel_hardware}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$HERE/build-root"

# rsync excludes applied to every repo (deps, build output, binaries, big generated files)
EXCLUDES=(
  --exclude '.git' --exclude 'node_modules' --exclude 'dist' --exclude 'build'
  --exclude '.next' --exclude 'out' --exclude 'obj' --exclude 'bin'
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' --exclude 'venv'
  --exclude 'package-lock.json' --exclude '*-lock.json' --exclude '*.min.js' --exclude '*.map'
  --exclude '.env' --exclude '.env.local'
  --exclude '*.dll' --exclude '*.exe' --exclude '*.so' --exclude '*.onnx' --exclude '*.pt'
  --exclude '*.bin' --exclude '*.dat' --exclude '*.data' --exclude '*.traineddata'
  --exclude '*.mp4' --exclude '*.mov' --exclude '*.wav' --exclude '*.mp3'
  --exclude '*.ico' --exclude '*.bmp' --exclude '*.jpg' --exclude '*.jpeg'
  --exclude '*.png' --exclude '*.webp' --exclude '*.gif' --exclude '*.pdf' --exclude '*.resx'
  --exclude 'db/backup/data.ts'
)

say() { printf '\033[1;34m›\033[0m %s\n' "$*"; }

for name in WEB PMS AUDIO HW; do
  path="${!name}"
  [ -d "$path" ] || { echo "ERROR: $name repo not found at: $path" >&2; echo "Set it, e.g.  $name=/your/path ./scripts/build-graph.sh" >&2; exit 1; }
done

say "Resetting build root: $ROOT"
rm -rf "$ROOT"
mkdir -p "$ROOT"

say "Copying kiotel_web  ← $WEB"
rsync -a "${EXCLUDES[@]}" "$WEB/"   "$ROOT/kiotel_web/"

say "Copying kiotel-pms  ← $PMS"
rsync -a "${EXCLUDES[@]}" "$PMS/"   "$ROOT/kiotel-pms/"

say "Copying audio_services  ← $AUDIO"
rsync -a "${EXCLUDES[@]}" "$AUDIO/" "$ROOT/audio_services/"

# Hardware is special: the repo is a ClickOnce publish dump. Include ONLY the docs + the
# real app source (kiosk_Source), never the ~90 vendored DLLs / multi-GB OCR models.
say "Copying kiotel_hardware (docs + kiosk_Source only)  ← $HW"
mkdir -p "$ROOT/kiotel_hardware"
[ -f "$HW/README.md" ] && rsync -a "$HW/README.md" "$ROOT/kiotel_hardware/"
[ -d "$HW/docs" ]      && rsync -a "${EXCLUDES[@]}" "$HW/docs/" "$ROOT/kiotel_hardware/docs/"
HW_SRC="$HW/Application Files/core_kiosk/kiosk_Source"
if [ -d "$HW_SRC" ]; then
  rsync -a "${EXCLUDES[@]}" "$HW_SRC/" "$ROOT/kiotel_hardware/kiosk_Source/"
else
  echo "WARN: hardware source not found at: $HW_SRC — hardware app code will be missing." >&2
fi

# Drop the shared ignore file into the build root.
cp "$HERE/.graphifyignore" "$ROOT/.graphifyignore"

say "Done."
echo
echo "Build root ready at:  $ROOT"
du -sh "$ROOT" 2>/dev/null || true
echo
echo "Next: build the graph (see RUNBOOK.md). Quickest path if you use Claude Code:"
echo "    graphify install         # one-time, registers the /graphify skill"
echo "    /graphify $ROOT --mode deep"
echo
echo "Then move build-root/graphify-out/ up to ./graphify-out/ and commit."
