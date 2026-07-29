"""Validate a research-agent moment JSON result."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.moment_research import load_moment_research  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    result = load_moment_research(args.path)
    events = result.to_events()
    output = {
        "run_date": result.run_date.isoformat(),
        "candidate_count": len(result.candidates),
        "events": [event.to_dict() for event in events],
    }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
