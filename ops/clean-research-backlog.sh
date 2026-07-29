#!/usr/bin/env bash
# Clear unpublished research backlog (festival/tour/moment) from SQLite + pending JSON.
#
# Default is dry-run. Pass --apply to delete.
#
# Usage:
#   ./ops/clean-research-backlog.sh
#   ./ops/clean-research-backlog.sh --days 3 --apply
#   ./ops/clean-research-backlog.sh --all-unpublished --apply
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
exec "$PYTHON" scripts/clean_research_backlog.py "$@"
