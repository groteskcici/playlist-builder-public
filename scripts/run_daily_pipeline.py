"""Run the unified daily playlist pipeline end-to-end."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.daily_pipeline import (  # noqa: E402
    DailyPipelineConfig,
    env_daily_cap,
    env_daily_floor,
    env_stretch_seconds,
    run_daily_pipeline,
)
from playlist_builder.persistence import store_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Unified daily pipeline: Genius album discovery + research generation/"
            "import + AI competition gates + publish (cap 20 / floor 5)."
        )
    )
    parser.add_argument("--market", default=os.environ.get("PLAYLIST_MARKET", "DE"))
    parser.add_argument("--daily-cap", type=int, default=None)
    parser.add_argument("--daily-floor", type=int, default=None)
    parser.add_argument("--stretch-seconds", type=float, default=None)
    parser.add_argument("--genius-future-months", type=int, default=None)
    parser.add_argument("--skip-discover", action="store_true")
    parser.add_argument("--skip-research-gen", action="store_true")
    parser.add_argument("--skip-publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = DailyPipelineConfig(
        market=args.market,
        daily_cap=args.daily_cap if args.daily_cap is not None else env_daily_cap(),
        daily_floor=args.daily_floor if args.daily_floor is not None else env_daily_floor(),
        stretch_seconds=(
            args.stretch_seconds if args.stretch_seconds is not None else env_stretch_seconds()
        ),
        genius_future_months=(
            args.genius_future_months
            if args.genius_future_months is not None
            else int(os.environ.get("GENIUS_FUTURE_MONTHS", "5"))
        ),
        dry_run=args.dry_run,
        skip_discover=args.skip_discover,
        skip_research_gen=args.skip_research_gen,
        skip_publish=args.skip_publish,
    )

    store = store_from_env()
    store.init_schema()
    result = run_daily_pipeline(store, config=config)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
    if result.fatal_error:
        return 2
    if not result.album_discovery_ok and result.published_count == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
