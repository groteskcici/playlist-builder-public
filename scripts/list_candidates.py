"""List persisted candidate state rows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--due-only", action="store_true")
    parser.add_argument("--publish-pending-only", action="store_true")
    parser.add_argument(
        "--score-action",
        choices=("ignore", "queue_for_review", "ready_to_publish"),
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    store = store_from_env()
    if args.due_only:
        rows = store.list_due_candidates(as_of=datetime.now(timezone.utc))
    else:
        rows = store.list_candidates(
            limit=args.limit,
            publish_pending_only=args.publish_pending_only,
        )

    if args.score_action:
        rows = [row for row in rows if row.score_action == args.score_action]

    output = [
        {
            "dedupe_key": row.dedupe_key,
            "artist_name": row.artist_name,
            "project_title": row.project_title,
            "event_date": row.event_date,
            "effective_status": row.effective_status,
            "score_action": row.score_action,
            "resolved_track_count": row.resolved_track_count,
            "resolved_track_uris": row.resolved_track_uris,
            "last_checked_at": row.last_checked_at.isoformat(),
            "next_check_at": row.next_check_at.isoformat(),
            "publish_pending": row.publish_pending,
            "spotify_playlist_uri": row.spotify_playlist_uri,
            "last_published_at": (
                row.last_published_at.isoformat() if row.last_published_at else None
            ),
        }
        for row in rows[: args.limit]
    ]
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
