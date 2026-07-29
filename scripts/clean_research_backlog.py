"""Clean unpublished research backlog from the candidate DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.research_backlog_cleanup import clean_research_backlog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove unpublished festival/tour/moment research candidates so the "
            "daily pipeline can regenerate clean titles. Defaults to dry-run."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Only remove research touched within the last N days (default: 3).",
    )
    parser.add_argument(
        "--all-unpublished",
        action="store_true",
        help="Ignore --days and remove every unpublished research candidate.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete matched rows / pending files (default is dry-run).",
    )
    parser.add_argument(
        "--keep-pending-files",
        action="store_true",
        help="Do not clear data/research/pending/*.json.",
    )
    args = parser.parse_args()

    store = store_from_env()
    result = clean_research_backlog(
        store,
        days=args.days,
        apply=args.apply,
        include_all_unpublished=args.all_unpublished,
        clear_pending_files=not args.keep_pending_files,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
    if result.dry_run:
        print(
            "\n[dry-run] no changes written; re-run with --apply to delete.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
