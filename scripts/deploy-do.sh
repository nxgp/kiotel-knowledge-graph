#!/usr/bin/env bash
#
# deploy-do.sh — build the MCP image for linux/amd64, push to DO Container
# Registry, and roll a new App Platform deployment. Use this after any graph
# rebuild to update the live team endpoint.
#
# Prereqs (one-time):
#   doctl auth init                 # authenticate doctl to the DO account
#   doctl registry create kiotel --subscription-tier starter --region nyc3
#   doctl apps create --spec .do/app.yaml   # first deploy only; prints the App ID
#
# Then, to update:  ./scripts/deploy-do.sh
set -euo pipefail

APP_ID="${APP_ID:-5063fd52-c7eb-4db8-a82c-0d9b3c291b4f}"
IMAGE="registry.digitalocean.com/kiotel/kiotel-graph-mcp:latest"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

echo "› doctl registry login"
doctl registry login >/dev/null

# CRITICAL: --platform linux/amd64. App Platform is amd64; an arm64 image
# (default on Apple Silicon) crashes with "exec format error".
echo "› building linux/amd64 image and pushing to DOCR (QEMU cross-build, a few min)"
docker buildx build --platform linux/amd64 -t "$IMAGE" --push .

echo "› rolling a new App Platform deployment"
doctl apps create-deployment "$APP_ID" --wait --format Phase,Progress --no-header

URL="$(doctl apps get "$APP_ID" --format DefaultIngress --no-header)"
echo "› live: $URL   (MCP: $URL/mcp · dashboard: $URL/dashboard)"
echo "› health: $(curl -s "$URL/healthz")"
