#!/usr/bin/env bash
# Import research JSON files and publish playlists for all imported research candidates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LOCK_FILE="${PLAYLIST_PIPELINE_LOCK_FILE:-/tmp/playlist_builder.pipeline.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[research-pipeline] skipped: another pipeline run holds $LOCK_FILE"
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
RESEARCH_KIND="${RESEARCH_KIND:-}"
PUBLISH_LIMIT="${RESEARCH_PUBLISH_LIMIT:-20}"
DRY_RUN=0

ARGS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run-publish) DRY_RUN=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done

mkdir -p data/research/pending data/research/imported

IMPORT_ARGS=()
if [[ -n "$RESEARCH_KIND" ]]; then
  IMPORT_ARGS+=(--kind "$RESEARCH_KIND")
fi

if [[ "${#ARGS[@]}" -eq 0 ]]; then
  shopt -s nullglob
  FILES=(data/research/pending/*.json)
  shopt -u nullglob
else
  FILES=("${ARGS[@]}")
fi

if [[ "${#FILES[@]}" -eq 0 ]]; then
  echo "[research-pipeline] no JSON files found"
  exit 0
fi

echo "[research-pipeline] importing ${#FILES[@]} file(s)"
"$PYTHON" scripts/import_research_events.py --archive-dir data/research/imported "${IMPORT_ARGS[@]}" "${FILES[@]}"

echo "[research-pipeline] publishing imported research playlists"
PUBLISH_ARGS=(--limit "$PUBLISH_LIMIT" --market "$MARKET" --require-competition-pass)
if [[ "$DRY_RUN" -eq 1 ]]; then
  PUBLISH_ARGS+=(--dry-run)
fi
"$PYTHON" scripts/publish_research_playlists.py "${PUBLISH_ARGS[@]}"

echo "[research-pipeline] done"
