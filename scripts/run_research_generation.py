"""Generate festival/tour/moment research JSON with local Codex CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.ai.codex_cli import CodexCliConfig, CodexCliError, CodexCliRunner  # noqa: E402
from playlist_builder.ai.research_generation import (  # noqa: E402
    berlin_run_date,
    generate_research_json,
)
from playlist_builder.persistence import store_from_env  # noqa: E402
from playlist_builder.research_dedupe import (  # noqa: E402
    extend_research_index_from_payload,
    load_existing_research_index,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research generation via local Codex CLI (no OpenClaw)."
    )
    parser.add_argument(
        "--kind",
        choices=("festival", "tour", "moment", "all"),
        default="all",
    )
    parser.add_argument("--run-date", help="YYYY-MM-DD (default: today Europe/Berlin)")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Per-kind Codex timeout (default 900)",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Skip filtering against existing DB research candidates",
    )
    args = parser.parse_args()

    kinds = ("festival", "tour", "moment") if args.kind == "all" else (args.kind,)
    run_date = args.run_date or berlin_run_date()
    runner = CodexCliRunner(
        config=CodexCliConfig(timeout_seconds=args.timeout_seconds, sandbox="read-only")
    )

    try:
        runner.ensure_ready()
    except CodexCliError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=True), file=sys.stderr)
        return 1

    store = None if args.no_dedupe else store_from_env()
    existing_index = None if store is None else load_existing_research_index(store)

    results = []
    errors = []
    for kind in kinds:
        try:
            result = generate_research_json(
                kind,  # type: ignore[arg-type]
                runner=runner,
                run_date=run_date,
                store=store,
                existing_index=existing_index,
            )
            results.append(
                {
                    "kind": result.kind,
                    "run_date": result.run_date,
                    "output_path": str(result.output_path),
                    "candidate_count": result.candidate_count,
                    "dropped_count": result.dropped_count,
                    "dropped": list(result.dropped),
                }
            )
            if existing_index is not None and result.candidate_count:
                payload = json.loads(result.output_path.read_text(encoding="utf-8"))
                existing_index = extend_research_index_from_payload(existing_index, payload)
        except Exception as exc:
            errors.append({"kind": kind, "error": str(exc)})

    print(
        json.dumps(
            {
                "run_date": run_date,
                "generated": results,
                "errors": errors,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 1 if errors and not results else 0


if __name__ == "__main__":
    raise SystemExit(main())
