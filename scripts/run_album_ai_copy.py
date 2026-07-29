"""Generate album playlist descriptions via local Codex CLI and save applied plans."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.ai.album_planner import (  # noqa: E402
    applied_plan_path,
    build_album_copy_jobs,
    generate_and_save_album_copy,
    load_applied_plan,
)
from playlist_builder.ai.codex_cli import CodexCliError, CodexCliRunner  # noqa: E402
from playlist_builder.ai.queue import AiQueue  # noqa: E402
from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.scoring import ScoreAction  # noqa: E402
from playlist_builder.spotify_album_watcher import SpotifyWebAPIClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build album track lists in Python, then write descriptions with local Codex CLI."
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--market", default="DE")
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Include queue_for_review candidates (default: publishable only)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Fail instead of writing a template description when Codex errors",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if an applied plan already exists",
    )
    args = parser.parse_args()

    store = store_from_env()
    queue = AiQueue.from_env()
    queue.ensure_dirs()
    client = SpotifyWebAPIClient.from_env()
    runner = CodexCliRunner()

    try:
        runner.ensure_ready()
    except CodexCliError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=True), file=sys.stderr)
        return 1

    candidates = store.list_candidates(limit=args.limit, publish_pending_only=True)
    applied: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for candidate in candidates:
        if candidate.score_action not in {
            ScoreAction.READY_TO_PUBLISH.value,
            ScoreAction.QUEUE_FOR_REVIEW.value,
        }:
            continue
        if args.include_review is False and candidate.score_action != "ready_to_publish":
            if candidate.score_action == "queue_for_review":
                skipped.append(
                    {
                        "dedupe_key": candidate.dedupe_key,
                        "reason": "queue_for_review (use --include-review)",
                    }
                )
                continue

        existing = load_applied_plan(queue, candidate.dedupe_key)
        if existing and not args.force:
            skipped.append({"dedupe_key": candidate.dedupe_key, "reason": "applied_plan_exists"})
            continue

        payload = store.get_latest_payload(candidate.dedupe_key) or {}
        try:
            job = build_album_copy_jobs(
                candidate,
                payload,
                client,
                market=args.market,
            )
        except ValueError as exc:
            skipped.append({"dedupe_key": candidate.dedupe_key, "reason": str(exc)})
            continue

        if existing and args.force:
            path = applied_plan_path(queue, candidate.dedupe_key)
            if path.is_file():
                path.unlink()

        plan = generate_and_save_album_copy(
            job,
            queue,
            runner=runner,
            fallback_on_error=not args.no_fallback,
        )
        applied.append(
            {
                "dedupe_key": plan.dedupe_key,
                "source": plan.source,
                "title": plan.title,
                "track_count": len(plan.track_uris),
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
