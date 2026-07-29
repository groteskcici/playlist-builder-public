#!/usr/bin/env bash
# Generate festival/tour/moment research JSON via local Codex CLI.
#
# Usage:
#   ./ops/run-research-generation.sh
#   ./ops/run-research-generation.sh festival
#   ./ops/run-research-generation.sh tour
#   ./ops/run-research-generation.sh moment
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

unset OPENAI_API_KEY CODEX_API_KEY || true

PYTHON="${PYTHON:-python3}"
KIND="${1:-all}"

echo "[research-generation] kind=$KIND via local Codex CLI"
"$PYTHON" scripts/run_research_generation.py --kind "$KIND"
echo "[research-generation] done"
