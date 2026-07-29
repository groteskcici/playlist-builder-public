#!/usr/bin/env bash
# DEPRECATED: research generation now uses local Codex CLI.
# Prefer: ./ops/run-research-generation.sh
#
# This script only removes legacy OpenClaw research-generation crons if present.
set -euo pipefail

PLAYLIST_ROOT="${PLAYLIST_ROOT:-$HOME/clawd/projects/playlist_builder}"

echo "OpenClaw research generation cron is deprecated."
echo "Generate research JSON via local Codex CLI:"
echo "  cd $PLAYLIST_ROOT && ./ops/run-research-generation.sh"
echo "  # or: python3 scripts/run_research_generation.py --kind all"
echo ""

remove_if_exists() {
  local name="$1"
  local existing
  existing="$(openclaw cron list 2>/dev/null | grep -F " ${name} " | head -1 | awk '{print $1}' || true)"
  if [[ -n "${existing:-}" ]]; then
    echo "Removing legacy OpenClaw cron: $name ($existing)"
    openclaw cron rm "$existing" || true
  else
    echo "No OpenClaw cron named $name"
  fi
}

remove_if_exists "playlist-research-festival"
remove_if_exists "playlist-research-tour"
remove_if_exists "playlist-research-moment"

echo "Done. System cron should call run-research-generation.sh (see install-system-cron.sh)."
