"""Run research playlist competition review via local Codex CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.ai.codex_cli import CodexCliError, CodexCliRunner  # noqa: E402
from playlist_builder.ai.research_competition import (  # noqa: E402
    MissingResearchCompetitionQueriesError,
    SpotifyBearerCompetitionClient,
    generate_research_competition_with_retitle,
)
from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.spotify_album_watcher import SpotifyWebAPIClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spotify search in Python; Codex judges competition for research titles."
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--market", default="DE")
    parser.add_argument("--dedupe-key")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if competition_evaluation already exists on the payload",
    )
    parser.add_argument(
        "--direct-access-token",
        action="store_true",
        help="Use SPOTIFY_ACCESS_TOKEN directly for read-only competition lookups",
    )
    args = parser.parse_args()

    store = store_from_env()
    if args.direct_access_token:
        client = SpotifyBearerCompetitionClient(os.environ.get("SPOTIFY_ACCESS_TOKEN", ""))
    else:
        client = SpotifyWebAPIClient.from_env()
    runner = CodexCliRunner()

    try:
        runner.ensure_ready()
    except CodexCliError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=True), file=sys.stderr)
        return 1

    if args.dedupe_key:
        candidates = [
            row
            for row in store.list_candidates(limit=500, publish_pending_only=False)
            if row.dedupe_key == args.dedupe_key
        ]
    else:
        candidates = [
            row
            for row in store.list_candidates(
                limit=max(args.limit * 5, 100),
                publish_pending_only=True,
            )
            if row.effective_status == "research_candidate"
        ][: args.limit]

    applied = []
    skipped = []
    for candidate in candidates:
        payload = store.get_latest_payload(candidate.dedupe_key) or {}
        if payload.get("competition_evaluation") and not args.force:
            skipped.append(
                {
                    "dedupe_key": candidate.dedupe_key,
                    "reason": "competition_evaluation_exists",
                }
            )
            continue

        try:
            result = generate_research_competition_with_retitle(
                candidate,
                payload,
                store,
                client=client,
                runner=runner,
                market=args.market,
            )
        except MissingResearchCompetitionQueriesError as exc:
            skipped.append(
                {
                    "dedupe_key": candidate.dedupe_key,
                    "reason": "missing_ai_competition_queries",
                    "detail": str(exc),
                }
            )
            continue
        except Exception as exc:
            skipped.append({"dedupe_key": candidate.dedupe_key, "reason": str(exc)})
            continue

        applied.append(
            {
                "dedupe_key": result.dedupe_key,
                "competition_verdict": result.competition_verdict,
                "revised_title": result.revised_title,
                "title": result.title,
            }
        )

    print(
        json.dumps(
            {
                "applied_count": len(applied),
                "applied": applied,
                "skipped": skipped,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
