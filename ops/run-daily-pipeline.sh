#!/usr/bin/env bash
# Unified daily playlist pipeline (discover → compete → build → publish).
#
# Prerequisites:
#   - Codex CLI installed + `codex login` (ChatGPT/Pro)
#   - Do not set OPENAI_API_KEY / CODEX_API_KEY
#   - Optional: GENIUS_FETCH_PROXY_1..5, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#
# Usage:
#   ./ops/run-daily-pipeline.sh
#   ./ops/run-daily-pipeline.sh --dry-run
#   ./ops/run-daily-pipeline.sh --skip-discover
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LOCK_FILE="${PLAYLIST_PIPELINE_LOCK_FILE:-/tmp/playlist_builder.pipeline.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[daily-pipeline] skipped: another pipeline run holds $LOCK_FILE"
  exit 0
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

unset OPENAI_API_KEY CODEX_API_KEY || true

export PLAYLIST_REQUIRE_AI_PLAN="${PLAYLIST_REQUIRE_AI_PLAN:-1}"
PYTHON="${PYTHON:-python3}"

mkdir -p logs data/ai/applied data/research/pending data/research/imported

echo "[daily-pipeline] starting unified daily run"
"$PYTHON" scripts/run_daily_pipeline.py "$@"
echo "[daily-pipeline] done"
