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

# ---- wave-2 repos (added 2026-08-16): the rest of the Kiotel estate ----
# One flat folder of repo snapshots; override with NEWREPOS=/path if yours differ.
NEWREPOS="${NEWREPOS:-$HOME/Documents/kiotel- code}"
# build-root dir name -> source dir name (clean underscore names keep graph node IDs sane)
declare -a WAVE2=(
  "kiotel_space:kiotel-space-main"
  "kiotel_admin:kiotel-admin-main"
  "kiotel_portal_front:Kiotel_portal_front-main"
  "chatbot:chatbot-main"
  "hr_kiotel_backend:hr_kiotel_backend-main"
  "kiotel_lox:kiotel_lox-main"
  "stt_tts_inhouse:stt-tts-inhouse-main"
  "guest_translation:guest_translation-main"
  "speech_to_text_serverless:speech-to-text-serverless-main"
  "kiotel_pms_autofill:kiotel_pms_autofill-master"
  # service-kiotel-space-calls-main is an empty placeholder (only .gitignore) - skipped
)
if [ -d "$NEWREPOS" ]; then
  for pair in "${WAVE2[@]}"; do
    dest="${pair%%:*}"; src="${pair#*:}"
    if [ -d "$NEWREPOS/$src" ]; then
      say "Copying $dest  ← $NEWREPOS/$src"
      rsync -a "${EXCLUDES[@]}" "$NEWREPOS/$src/" "$ROOT/$dest/"
    else
      echo "WARN: wave-2 repo not found: $NEWREPOS/$src — skipping." >&2
    fi
  done
else
  echo "WARN: NEWREPOS dir not found at: $NEWREPOS — wave-2 repos skipped." >&2
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
