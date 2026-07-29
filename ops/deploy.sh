#!/usr/bin/env bash
# Idempotent VPS deploy: pull, deps, schema, dirs.
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
mkdir -p data logs data/ai/applied data/research/pending data/research/imported

if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -e .
"$PYTHON" scripts/init_db.py

echo "[deploy] OK — DATABASE_URL=${DATABASE_URL:-unset}"
