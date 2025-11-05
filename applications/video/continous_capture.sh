#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail
cd "$(dirname "$0")"

# Load env to get REPO_DIR
if [ -f ../../.env ]; then
  set -a
  . ../../.env
  set +a
fi

# Fallback REPO_DIR to repo root (two levels up) if not set
if [ -z "${REPO_DIR:-}" ]; then
  REPO_DIR="$(cd ../.. >/dev/null 2>&1 && pwd)"
fi

OUT_DIR="${REPO_DIR%/}/run/images"
mkdir -p "$OUT_DIR"

CAMERA_ID="${CAMERA_ID:-0}"
CAPTURE_INTERVAL_S="${CAPTURE_INTERVAL_S:-1}"

while true; do
  ts=$(date -u +%Y%m%dT%H%M%S%NZ)
  out_path="$OUT_DIR/photo_${ts}.jpg"
  termux-camera-photo -c "$CAMERA_ID" "$out_path"
  sleep "$CAPTURE_INTERVAL_S"
done
