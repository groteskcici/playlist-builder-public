#!/usr/bin/env bash
# Daily album discovery + due-candidate refresh (Genius + prerelease URI resolve).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LOCK_FILE="${PLAYLIST_PIPELINE_LOCK_FILE:-/tmp/playlist_builder.pipeline.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[album-scheduler] skipped: another pipeline run holds $LOCK_FILE"
  exit 0
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PYTHON="${PYTHON:-python3}"
MARKET="${PLAYLIST_MARKET:-DE}"
GENIUS_FUTURE_MONTHS="${GENIUS_FUTURE_MONTHS:-5}"

echo "[album-scheduler] discover + refresh due (market=$MARKET, future_months=$GENIUS_FUTURE_MONTHS)"
"$PYTHON" scripts/run_album_scheduler.py \
  --discover-genius \
  --future-months "$GENIUS_FUTURE_MONTHS" \
  --market "$MARKET"

echo "[album-scheduler] done"
