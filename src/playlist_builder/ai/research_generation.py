"""Generate festival/tour/moment research JSON via local Codex CLI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from playlist_builder.ai.codex_cli import CodexCliConfig, CodexCliRunner, schema_path
from playlist_builder.festival_research import load_festival_research
from playlist_builder.moment_research import load_moment_research
from playlist_builder.persistence.env import project_root
from playlist_builder.research_dedupe import (
    ExistingResearchIndex,
    filter_research_payload,
    load_existing_research_index,
)
from playlist_builder.tour_research import load_tour_research

ResearchKind = Literal["festival", "tour", "moment"]

_SECTION_MARKERS = {
    "festival": "## 1) Festival Research Prompt",
    "tour": "## 2) Tour Research Prompt",
    "moment": "## 3) Moment Research Prompt",
}

_SCHEMA_FILES = {
    "festival": "festival_research.json",
    "tour": "tour_research.json",
    "moment": "moment_research.json",
}

_VALIDATORS: dict[ResearchKind, Callable[[Path], Any]] = {
    "festival": load_festival_research,
    "tour": load_tour_research,
    "moment": load_moment_research,
}


@dataclass(frozen=True, slots=True)
class ResearchGenerationResult:
    kind: ResearchKind
    run_date: str
    output_path: Path
    candidate_count: int
    dropped_count: int = 0
    dropped: tuple[dict[str, str], ...] = ()


def prompts_path() -> Path:
    return project_root() / "ops" / "RESEARCH_PROMPTS.md"


def pending_dir() -> Path:
    return project_root() / "data" / "research" / "pending"


def berlin_run_date() -> str:
    return datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()


def extract_prompt_section(kind: ResearchKind, *, prompts_file: Path | None = None) -> str:
    path = prompts_file or prompts_path()
    text = path.read_text(encoding="utf-8")
    start_marker = _SECTION_MARKERS[kind]
    start = text.find(start_marker)
    if start < 0:
        raise ValueError(f"missing research prompt section for {kind}")

    next_markers = [marker for other, marker in _SECTION_MARKERS.items() if other != kind]
    end = len(text)
    for marker in next_markers:
        idx = text.find(marker, start + len(start_marker))
        if idx >= 0:
            end = min(end, idx)
    return text[start:end].strip()


def build_research_generation_prompt(
    kind: ResearchKind,
    *,
    run_date: str,
    output_filename: str,
    prompts_file: Path | None = None,
) -> str:
    section = extract_prompt_section(kind, prompts_file=prompts_file)
    section = section.replace("{{RUN_DATE}}", run_date)
    section = section.replace("{{FESTIVAL_OUTPUT_FILE}}", output_filename)
    section = section.replace("{{TOUR_OUTPUT_FILE}}", output_filename)
    section = section.replace("{{MOMENT_OUTPUT_FILE}}", output_filename)

    section = re.sub(
        r"Output instructions:.*?JSON shape:",
        (
            "Output instructions:\n"
            "- Return ONLY the final JSON object (no markdown fences, no chat prose).\n"
            "- Do not write files yourself; the host script will save the JSON.\n"
            "- Prefer fewer candidates over inventing weak ones.\n"
            "- Prefer events not already widely covered in recent playlist research; "
            "the host will dedupe against existing candidates.\n"
            "- Use web search / browsing to verify sources when available.\n\n"
            "JSON shape:"
        ),
        section,
        count=1,
        flags=re.DOTALL,
    )
    return (
        f"{section}\n\n"
        f"Today's run_date must be exactly: {run_date}\n"
        "Return the complete JSON object now.\n"
    )


def generate_research_json(
    kind: ResearchKind,
    *,
    runner: CodexCliRunner | None = None,
    run_date: str | None = None,
    output_dir: Path | None = None,
    prompts_file: Path | None = None,
    store: Any | None = None,
    existing_index: ExistingResearchIndex | None = None,
) -> ResearchGenerationResult:
    resolved_date = run_date or berlin_run_date()
    out_dir = output_dir or pending_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{kind}_{resolved_date}.json"
    filename = output_path.name

    active_runner = runner or CodexCliRunner(
        config=CodexCliConfig(timeout_seconds=900, sandbox="read-only")
    )
    prompt = build_research_generation_prompt(
        kind,
        run_date=resolved_date,
        output_filename=filename,
        prompts_file=prompts_file,
    )
    payload = active_runner.run_json(
        prompt=prompt,
        schema_file=schema_path(_SCHEMA_FILES[kind]),
        cwd=project_root(),
    )
    if str(payload.get("run_date", "")).strip() != resolved_date:
        payload = dict(payload)
        payload["run_date"] = resolved_date

    dropped: tuple[dict[str, str], ...] = ()
    dropped_count = 0
    index = existing_index
    if index is None and store is not None:
        index = load_existing_research_index(store)
    if index is not None:
        deduped = filter_research_payload(kind, payload, index)
        payload = deduped.payload
        dropped = deduped.dropped
        dropped_count = deduped.dropped_count

    output_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    validated = _VALIDATORS[kind](output_path)
    return ResearchGenerationResult(
        kind=kind,
        run_date=resolved_date,
        output_path=output_path,
        candidate_count=len(validated.candidates),
        dropped_count=dropped_count,
        dropped=dropped,
    )
