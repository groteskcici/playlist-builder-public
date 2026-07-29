#!/usr/bin/env bash
# Install/update user crontab entries for the current playlist_builder pipeline.
# Preserves unrelated cron jobs, removes stale playlist_builder leftovers, and
# replaces the managed block idempotently.
set -euo pipefail

REPO_ROOT="${PLAYLIST_ROOT:-$HOME/clawd/projects/playlist_builder}"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

BEGIN_MARKER="# BEGIN PLAYLIST_BUILDER_MANAGED"
END_MARKER="# END PLAYLIST_BUILDER_MANAGED"

existing="$(crontab -l 2>/dev/null || true)"

cleaned="$(printf '%s\n' "$existing" | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
  $0 == begin {skip=1; next}
  $0 == end {skip=0; next}
  skip != 1 {print}
')"

cleaned="$(printf '%s\n' "$cleaned" | grep -vE '^[[:space:]]*# (Ticketmaster radar \(tours \+ festivals\)|Album discovery \(Genius \+ due refresh\)|Album enqueue → merge → publish \(OpenClaw fills descriptions separately every 30m\)|Tour setlists \(setlist\.fm after first show\)|Festival lineups|Mega-events \(curated calendar\)|Playlist pipeline health alerts|Playlist bootstrap catch-up \(prepare-only, no publishing\))$' || true)"

cleaned="$(printf '%s\n' "$cleaned" | grep -vE '/clawd/projects/playlist_builder/(openclaw|ops)/run-(album-scheduler|album-pipeline|research-import|research-pipeline|research-generation|daily-pipeline)\.sh' || true)"

managed_block=$(cat <<EOF
$BEGIN_MARKER
# Playlist Builder — unified daily pipeline (local Codex CLI)
# Stretch sleeps live inside the run. Cap 20 / floor 5. Soft-fails album discovery.
0 7 * * * cd $REPO_ROOT && ./ops/run-daily-pipeline.sh >> logs/daily-pipeline.log 2>&1
$END_MARKER
EOF
)

new_crontab="$(printf '%s\n\n%s\n' "$cleaned" "$managed_block" | sed '/^[[:space:]]*$/N;/^\n$/D')"

printf '%s\n' "$new_crontab" | crontab -

echo "Installed playlist_builder cron block into user crontab."
