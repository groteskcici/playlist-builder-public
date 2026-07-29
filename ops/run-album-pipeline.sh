#!/usr/bin/env bash
# Album pipeline: local Codex descriptions → publish.
#
# Prerequisites on this machine:
#   - Codex CLI installed
#   - `codex login` with ChatGPT/Pro (do not set OPENAI_API_KEY / CODEX_API_KEY)
#
# Usage:
#   ./ops/run-album-pipeline.sh
#   ./ops/run-album-pipeline.sh --dry-run-publish
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LOCK_FILE="${PLAYLIST_PIPELINE_LOCK_FILE:-/tmp/playlist_builder.pipeline.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[album-pipeline] skipped: another pipeline run holds $LOCK_FILE"
  exit 0
fi

# Load .env if present (VPS)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Prefer ChatGPT subscription auth for Codex
unset OPENAI_API_KEY CODEX_API_KEY || true

export PLAYLIST_REQUIRE_AI_PLAN="${PLAYLIST_REQUIRE_AI_PLAN:-1}"
PYTHON="${PYTHON:-python3}"
ALBUM_PUBLISH_LIMIT="${ALBUM_PUBLISH_LIMIT:-10}"
DRY_PUBLISH=0

for arg in "$@"; do
  case "$arg" in
    --dry-run-publish) DRY_PUBLISH=1 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run-publish]"
      echo "Note: prefer ./ops/run-daily-pipeline.sh for the unified daily path."
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

echo "[album-pipeline] generate album copy via local Codex CLI"
"$PYTHON" scripts/run_album_ai_copy.py --include-review

# ALBUM_PUBLISH_LIMIT=0 is an emergency brake only (not the normal daily state).
if [[ "$ALBUM_PUBLISH_LIMIT" -le 0 ]]; then
  echo "[album-pipeline] publish skipped (ALBUM_PUBLISH_LIMIT=$ALBUM_PUBLISH_LIMIT emergency brake)"
else
  PUBLISH_ARGS=(--include-review --limit "$ALBUM_PUBLISH_LIMIT")
  if [[ "$DRY_PUBLISH" -eq 1 ]]; then
    PUBLISH_ARGS+=(--dry-run)
  fi

  echo "[album-pipeline] publish pending playlists (PLAYLIST_REQUIRE_AI_PLAN=$PLAYLIST_REQUIRE_AI_PLAN, limit=$ALBUM_PUBLISH_LIMIT)"
  "$PYTHON" scripts/publish_pending_playlists.py "${PUBLISH_ARGS[@]}"
fi

echo "[album-pipeline] done"
