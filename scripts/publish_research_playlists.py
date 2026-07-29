"""Publish Spotify playlists for imported research candidates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.spotify_publisher import (  # noqa: E402
    PlaylistPublishSkipError,
    SpotifyPublisher,
)
from playlist_builder.playlist_competition import PlaylistCompetitionSkipError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--market", default="DE")
    parser.add_argument(
        "--require-competition-pass",
        action="store_true",
        help="Only publish research candidates that have an AI competition verdict of 'publish'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build playlist plans without calling Spotify write APIs",
    )
    args = parser.parse_args()

    store = store_from_env()
    publisher = SpotifyPublisher.from_env(market=args.market)
    candidates = [
        candidate
        for candidate in store.list_candidates(limit=args.limit * 5, publish_pending_only=True)
        if candidate.effective_status == "research_candidate"
    ][: args.limit]

    results = []
    skipped = []
    for candidate in candidates:
        payload = store.get_latest_payload(candidate.dedupe_key) or {}
        if args.require_competition_pass:
            evaluation = payload.get("competition_evaluation")
            verdict = None
            if isinstance(evaluation, dict):
                verdict = str(evaluation.get("competition_verdict") or "").strip().lower()
            if verdict != "publish":
                skipped.append(
                    {
                        "dedupe_key": candidate.dedupe_key,
                        "reason": "missing_or_non_publish_competition_verdict",
                        "competition_verdict": verdict,
                    }
                )
                continue
        try:
            result = publisher.publish_candidate(candidate, payload, dry_run=args.dry_run)
        except (PlaylistPublishSkipError, PlaylistCompetitionSkipError) as exc:
            skipped.append({"dedupe_key": candidate.dedupe_key, "reason": str(exc)})
            continue
        results.append(result)
        if not args.dry_run and result.playlist_id and result.playlist_uri:
            store.mark_published(
                candidate.dedupe_key,
                playlist_id=result.playlist_id,
                playlist_uri=result.playlist_uri,
                published_at=datetime.now(timezone.utc),
                publish_slot=result.publish_slot,
            )

    output = {
        "settings": {
            "limit": args.limit,
            "market": args.market,
            "require_competition_pass": args.require_competition_pass,
            "dry_run": args.dry_run,
        },
        "published_count": len(results),
        "skipped_count": len(skipped),
        "results": [result.to_dict() for result in results],
        "skipped": skipped,
    }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
