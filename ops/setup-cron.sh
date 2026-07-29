#!/usr/bin/env bash
# DEPRECATED: album AI copy now uses local Codex CLI in this repo.
# Prefer: python scripts/run_album_ai_copy.py
#
# This script only removes the old OpenClaw playlist-ai-worker cron if present.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAYLIST_ROOT="${PLAYLIST_ROOT:-$HOME/clawd/projects/playlist_builder}"

echo "playlist-ai-worker OpenClaw cron is deprecated."
echo "Album descriptions run via local Codex CLI:"
echo "  cd $PLAYLIST_ROOT && python3 scripts/run_album_ai_copy.py --include-review"
echo "  # or: ./ops/run-album-pipeline.sh"
echo ""
echo "Prerequisites on the machine that runs the pipeline:"
echo "  - install Codex CLI"
echo "  - codex login  (ChatGPT/Pro — do not set OPENAI_API_KEY / CODEX_API_KEY)"
echo ""

EXISTING="$(openclaw cron list 2>/dev/null | grep -F 'playlist-ai-worker' | head -1 | awk '{print $1}' || true)"
if [[ -n "${EXISTING:-}" ]]; then
  echo "Removing legacy OpenClaw cron job: $EXISTING"
  openclaw cron rm "$EXISTING" || true
else
  echo "No playlist-ai-worker OpenClaw cron found."
fi

SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/workspace/skills}"
if [[ -d "$SKILLS_DIR/playlist-ai-worker" ]]; then
  echo "Legacy skill still present at $SKILLS_DIR/playlist-ai-worker (safe to delete)."
fi

mkdir -p "$PLAYLIST_ROOT/data/ai/applied"
echo "Done. Applied plans directory: $PLAYLIST_ROOT/data/ai/applied"
echo "See ops/CHAT_TEST.md for a local smoke test."
