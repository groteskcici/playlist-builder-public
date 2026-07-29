"""Run scheduled album discovery and due-candidate refresh."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.album_scheduler import AlbumScheduler  # noqa: E402
from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.spotify_matcher import SpotifyMatcherConfig  # noqa: E402
from playlist_builder.spotify_prerelease_enrichment import (  # noqa: E402
    SpotifyPrereleaseEnrichmentConfig,
)


def _pre_release_window_days_from_env() -> int | None:
    raw = os.environ.get("PRE_RELEASE_WINDOW_DAYS", "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 0:
        raise ValueError("PRE_RELEASE_WINDOW_DAYS must not be negative")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Discover Genius upcoming releases and/or refresh persisted candidates "
            "whose next_check_at is due."
        )
    )
    parser.add_argument("--market", default="DE")
    parser.add_argument("--high-popularity", type=int, default=80)
    parser.add_argument("--review-popularity", type=int, default=65)
    parser.add_argument("--review-followers", type=int, default=1_000_000)
    parser.add_argument("--legacy-review-followers", type=int, default=5_000_000)
    parser.add_argument("--today", default=None, help="YYYY-MM-DD override")
    parser.add_argument(
        "--discover-genius",
        action="store_true",
        help="Fetch Genius release calendar and validate new candidates",
    )
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--month", type=int, default=None)
    parser.add_argument(
        "--future-months",
        type=int,
        default=5,
        help=(
            "Also fetch N months after the selected/current Genius calendar month "
            "(default 5 = current month plus the next 5)"
        ),
    )
    parser.add_argument("--genius-limit", type=int, default=25)
    parser.add_argument(
        "--no-refresh-due",
        action="store_true",
        help="Skip re-validation of candidates past next_check_at",
    )
    parser.add_argument("--due-limit", type=int, default=50)
    parser.add_argument(
        "--no-resolve-prereleases",
        action="store_true",
        help="Skip Spotify prerelease scraping during scheduled runs",
    )
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout-ms", type=int, default=45_000)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run validation without writing to the database",
    )
    args = parser.parse_args()

    if not args.discover_genius and args.no_refresh_due:
        parser.error("enable --discover-genius and/or leave due refresh enabled")

    today = date.fromisoformat(args.today) if args.today else date.today()
    store = store_from_env()
    pre_release_window_days = _pre_release_window_days_from_env()
    matcher_config = SpotifyMatcherConfig(
        market=args.market,
        high_value_popularity=args.high_popularity,
        review_value_popularity=args.review_popularity,
        review_value_followers=args.review_followers,
        legacy_review_followers=args.legacy_review_followers,
        pre_release_window_days=pre_release_window_days,
    )
    enricher_config = SpotifyPrereleaseEnrichmentConfig(
        pre_release_window_days=pre_release_window_days,
    )
    scheduler = AlbumScheduler.from_env(
        market=args.market,
        matcher_config=matcher_config,
        enricher_config=enricher_config,
        resolve_prereleases=not args.no_resolve_prereleases,
        headless=args.headless,
        timeout_ms=args.timeout_ms,
    )

    result = scheduler.run(
        store,
        today=today,
        discover_genius=args.discover_genius,
        genius_year=args.year,
        genius_month=args.month,
        genius_future_months=args.future_months,
        genius_limit=args.genius_limit,
        refresh_due=not args.no_refresh_due,
        due_limit=args.due_limit,
        persist=not args.dry_run,
    )

    output = {
        "settings": {
            "market": args.market,
            "today": today.isoformat(),
            "discover_genius": args.discover_genius,
            "year": args.year or today.year,
            "month": args.month or today.month,
            "future_months": args.future_months,
            "genius_limit": args.genius_limit,
            "refresh_due": not args.no_refresh_due,
            "due_limit": args.due_limit,
            "resolve_prereleases": not args.no_resolve_prereleases,
            "dry_run": args.dry_run,
        },
        **result.to_dict(),
    }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 1 if result.summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
