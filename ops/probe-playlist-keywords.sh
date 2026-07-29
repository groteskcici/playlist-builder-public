#!/usr/bin/env bash
# Read-only Spotify playlist SERP probes for keyword research.
#
# Usage:
#   ./ops/probe-playlist-keywords.sh --summary-only
#   ./ops/probe-playlist-keywords.sh --summary-only "reading festival 2026"
#   ./ops/probe-playlist-keywords.sh --must-keep documentary "dont look back in anger documentary"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PYTHON="${PYTHON:-python3}"
exec "$PYTHON" scripts/probe_playlist_keywords.py "$@"
