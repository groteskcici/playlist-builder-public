"""Import validated research JSON files into the candidate database."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.research_import import import_research_files  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--kind",
        choices=("festival", "tour", "moment"),
        default=None,
        help="Optional explicit research kind. By default, infer from candidate event_type.",
    )
    parser.add_argument(
        "--today",
        default=None,
        help="YYYY-MM-DD override for scheduling logic.",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help="Optional directory to move successfully imported files into.",
    )
    args = parser.parse_args()

    store = store_from_env()
    store.init_schema()
    checked_at = datetime.now(timezone.utc)
    today = date.fromisoformat(args.today) if args.today else checked_at.date()
    summary = import_research_files(
        list(args.paths),
        store=store,
        kind=args.kind,
        checked_at=checked_at,
        today=today,
        archive_dir=args.archive_dir,
    )
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
