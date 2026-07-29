"""Publish Spotify playlists for candidates flagged publish_pending."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.spotify_publisher import SpotifyPublisher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--market", default="DE")
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Also publish queue_for_review candidates (default: ready_to_publish only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build playlist plans without calling Spotify write APIs",
    )
    args = parser.parse_args()

    store = store_from_env()
    publisher = SpotifyPublisher.from_env(market=args.market)
    results = publisher.publish_pending(
        store,
        limit=args.limit,
        auto_only=not args.include_review,
        dry_run=args.dry_run,
    )

    output = {
        "settings": {
            "limit": args.limit,
            "market": args.market,
            "include_review": args.include_review,
            "dry_run": args.dry_run,
        },
        "published_count": len(results),
        "results": [result.to_dict() for result in results],
    }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
