"""AI helpers — Python picks tracks / searches; local Codex CLI writes judgments."""

from playlist_builder.ai.album_planner import (
    AppliedAlbumPlan,
    apply_album_copy_result,
    build_album_copy_jobs,
    build_album_track_list,
    build_fallback_applied_plan,
    deterministic_backfill,
    generate_and_save_album_copy,
    load_applied_plan,
    save_applied_plan,
    template_album_description,
    template_album_title,
)
from playlist_builder.ai.codex_cli import CodexCliError, CodexCliRunner
from playlist_builder.ai.queue import AiQueue
from playlist_builder.ai.research_competition import (
    AppliedResearchCompetition,
    apply_research_competition_result,
    build_research_competition_job,
    generate_and_persist_research_competition,
    persist_research_competition_result,
    research_competition_job_id,
    title_and_queries_from_payload,
)
from playlist_builder.ai.research_generation import (
    ResearchGenerationResult,
    generate_research_json,
)

__all__ = [
    "AiQueue",
    "AppliedAlbumPlan",
    "AppliedResearchCompetition",
    "CodexCliError",
    "CodexCliRunner",
    "ResearchGenerationResult",
    "apply_album_copy_result",
    "apply_research_competition_result",
    "build_album_copy_jobs",
    "build_album_track_list",
    "build_research_competition_job",
    "build_fallback_applied_plan",
    "deterministic_backfill",
    "generate_and_persist_research_competition",
    "generate_and_save_album_copy",
    "generate_research_json",
    "load_applied_plan",
    "persist_research_competition_result",
    "research_competition_job_id",
    "save_applied_plan",
    "template_album_description",
    "template_album_title",
    "title_and_queries_from_payload",
]
