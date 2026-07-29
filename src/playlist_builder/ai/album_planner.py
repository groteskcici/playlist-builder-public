"""Build album track lists in Python; local Codex CLI writes playlist copy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from playlist_builder.ai.catalog import build_artist_catalog_pool
from playlist_builder.ai.queue import AiQueue
from playlist_builder.ai.schemas import (
    AlbumCopyJob,
    AlbumPlanConstraints,
    AnchorTrack,
    CatalogTrack,
    JOB_TYPE_ALBUM_COPY,
    parse_album_copy_result,
)
from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.playlist_templates import _ordered_prerelease_track_uris


@dataclass(frozen=True, slots=True)
class AppliedAlbumPlan:
    job_id: str
    dedupe_key: str
    title: str
    description: str
    track_uris: tuple[str, ...]
    source: str
    rationale: str | None = None
    applied_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "dedupe_key": self.dedupe_key,
            "title": self.title,
            "description": self.description,
            "track_uris": list(self.track_uris),
            "track_count": len(self.track_uris),
            "source": self.source,
            "rationale": self.rationale,
            "applied_at": self.applied_at,
        }


def job_id_for_candidate(dedupe_key: str) -> str:
    return f"album_copy:{dedupe_key}"


def applied_plan_path(queue: AiQueue, dedupe_key: str) -> Path:
    safe_key = re.sub(r"[^a-zA-Z0-9._-]+", "_", dedupe_key)
    return queue._paths.applied_dir / f"{safe_key}.json"


def load_applied_plan(queue: AiQueue, dedupe_key: str) -> AppliedAlbumPlan | None:
    path = applied_plan_path(queue, dedupe_key)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return _applied_from_dict(payload)


def save_applied_plan(queue: AiQueue, plan: AppliedAlbumPlan) -> Path:
    queue.ensure_dirs()
    path = applied_plan_path(queue, plan.dedupe_key)
    path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return path


def build_album_copy_jobs(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    catalog_client: Any,
    *,
    market: str | None = None,
    constraints: AlbumPlanConstraints | None = None,
    checked_at: datetime | None = None,
) -> AlbumCopyJob:
    rules = constraints or AlbumPlanConstraints()
    anchor_tracks = _anchor_tracks(candidate, payload)
    if not anchor_tracks:
        raise ValueError(f"{candidate.dedupe_key} has no released singles to anchor playlist")

    artist_id = candidate.spotify_artist_id
    if not artist_id:
        raise ValueError(f"{candidate.dedupe_key} has no spotify_artist_id for catalog lookup")

    exclude_uris = {track.uri for track in anchor_tracks}
    catalog_pool = build_artist_catalog_pool(
        catalog_client,
        artist_id=artist_id,
        project_title=candidate.project_title,
        exclude_uris=exclude_uris,
        market=market,
    )
    track_uris = build_album_track_list(anchor_tracks, catalog_pool, rules)
    artist = candidate.artist_name.strip()
    project = candidate.project_title.strip()
    created = (checked_at or datetime.now(timezone.utc)).isoformat()

    return AlbumCopyJob(
        job_id=job_id_for_candidate(candidate.dedupe_key),
        dedupe_key=candidate.dedupe_key,
        artist_name=artist,
        project_title=project,
        effective_status=candidate.effective_status,
        event_date=candidate.event_date,
        title=template_album_title(artist, project, candidate.effective_status),
        track_uris=track_uris,
        anchor_track_names=tuple(track.name for track in anchor_tracks),
        backfill_count=max(0, len(track_uris) - len(anchor_tracks)),
        created_at=created,
    )


def build_album_track_list(
    anchor_tracks: tuple[AnchorTrack, ...] | list[AnchorTrack],
    catalog_pool: list[CatalogTrack] | tuple[CatalogTrack, ...],
    constraints: AlbumPlanConstraints | None = None,
) -> tuple[str, ...]:
    rules = constraints or AlbumPlanConstraints()
    anchor_uris = [track.uri for track in anchor_tracks]
    pool_uris = [track.uri for track in catalog_pool]

    target = max(rules.min_tracks, len(anchor_uris))
    target = min(target, rules.max_tracks)
    backfill_needed = max(0, target - len(anchor_uris))
    backfill = deterministic_backfill(
        pool_uris=pool_uris,
        anchor_uris=set(anchor_uris),
        already_selected=set(anchor_uris),
        needed=backfill_needed,
    )
    track_uris = tuple(anchor_uris + backfill)
    if len(track_uris) > rules.max_tracks:
        return track_uris[: rules.max_tracks]
    return track_uris


def template_album_title(
    artist_name: str,
    project_title: str,
    effective_status: str,
) -> str:
    del effective_status  # same title shape for pre-release and album drop
    from playlist_builder.playlist_templates import with_full_album_suffix

    return with_full_album_suffix(f"{project_title.strip()} – {artist_name.strip()}")


def template_album_description(
    artist_name: str,
    project_title: str,
    effective_status: str,
) -> str:
    if effective_status == "pre_release_candidate":
        text = (
            f"Released singles from {artist_name}'s upcoming album {project_title}, "
            "plus essential tracks from across their catalog."
        )
    else:
        text = (
            f"Essential {artist_name} playlist for the {project_title} album cycle — "
            "new singles first, then fan favorites from their discography."
        )
    return _clip(text, 300)


def apply_album_copy_result(
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    source: str = "codex_cli",
) -> AppliedAlbumPlan:
    if str(job.get("type", "")) != JOB_TYPE_ALBUM_COPY:
        raise ValueError(f"unsupported job type: {job.get('type')}")

    context = job.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("job missing context")

    track_uris = _track_uris_from_context(context)
    if not track_uris:
        raise ValueError("job missing track_uris")

    title = str(context.get("title", "")).strip()
    if not title:
        title = template_album_title(
            str(context.get("artist_name", "")),
            str(context.get("project_title", "")),
            str(context.get("effective_status", "")),
        )

    parsed = parse_album_copy_result(result)
    return AppliedAlbumPlan(
        job_id=str(job.get("id", "")),
        dedupe_key=str(context.get("dedupe_key", "")),
        title=_clip(title, 100),
        description=_clip(parsed["description"], 300),
        track_uris=track_uris,
        source=source,
        rationale=parsed.get("rationale"),
        applied_at=datetime.now(timezone.utc).isoformat(),
    )


def deterministic_backfill(
    *,
    pool_uris: list[str],
    anchor_uris: set[str],
    already_selected: set[str],
    needed: int,
) -> list[str]:
    if needed <= 0:
        return []

    selected: list[str] = []
    for uri in pool_uris:
        if uri in anchor_uris or uri in already_selected or uri in selected:
            continue
        selected.append(uri)
        if len(selected) >= needed:
            break
    return selected


def build_fallback_applied_plan(
    job: AlbumCopyJob,
    *,
    source: str = "template_fallback",
) -> AppliedAlbumPlan:
    return AppliedAlbumPlan(
        job_id=job.job_id,
        dedupe_key=job.dedupe_key,
        title=job.title,
        description=template_album_description(
            job.artist_name,
            job.project_title,
            job.effective_status,
        ),
        track_uris=job.track_uris,
        source=source,
        rationale="Template description; Codex copy unavailable.",
        applied_at=datetime.now(timezone.utc).isoformat(),
    )


def generate_and_save_album_copy(
    job: AlbumCopyJob,
    queue: AiQueue,
    *,
    runner: Any,
    fallback_on_error: bool = True,
) -> AppliedAlbumPlan:
    """Call Codex for album description and persist an applied plan."""

    from playlist_builder.ai.codex_cli import (
        CodexCliError,
        build_album_copy_prompt,
        schema_path,
        wrap_ok_output,
    )

    if load_applied_plan(queue, job.dedupe_key):
        existing = load_applied_plan(queue, job.dedupe_key)
        assert existing is not None
        return existing

    try:
        output = runner.run_json(
            prompt=build_album_copy_prompt(job.to_dict()),
            schema_file=schema_path("album_copy.json"),
        )
        plan = apply_album_copy_result(
            job.to_dict(),
            wrap_ok_output(output),
            source="codex_cli",
        )
    except (CodexCliError, ValueError, TypeError, KeyError):
        if not fallback_on_error:
            raise
        plan = build_fallback_applied_plan(job, source="template_fallback")

    save_applied_plan(queue, plan)
    return plan


def _anchor_tracks(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
) -> tuple[AnchorTrack, ...]:
    enrichment = payload.get("prerelease_enrichment")
    if isinstance(enrichment, Mapping):
        resolution = enrichment.get("resolution")
        if isinstance(resolution, Mapping):
            tracks = resolution.get("tracks")
            if isinstance(tracks, list):
                anchors: list[AnchorTrack] = []
                for track in tracks:
                    if not isinstance(track, Mapping):
                        continue
                    spotify_track = track.get("spotify_track")
                    if not isinstance(spotify_track, Mapping):
                        continue
                    uri = spotify_track.get("uri")
                    name = spotify_track.get("name") or track.get("title")
                    if isinstance(uri, str) and uri and isinstance(name, str) and name.strip():
                        anchors.append(AnchorTrack(uri=uri, name=name.strip()))
                if anchors:
                    return tuple(anchors)

    uris = _ordered_prerelease_track_uris(payload) or list(candidate.resolved_track_uris)
    return tuple(
        AnchorTrack(uri=uri, name=f"Track {index + 1}")
        for index, uri in enumerate(uris)
        if uri
    )


def _track_uris_from_context(context: Mapping[str, Any]) -> tuple[str, ...]:
    uris = context.get("track_uris")
    if not isinstance(uris, list):
        return ()
    return tuple(str(uri).strip() for uri in uris if str(uri).strip())


def _clip(value: str, max_len: int) -> str:
    text = value.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _applied_from_dict(payload: Mapping[str, Any]) -> AppliedAlbumPlan:
    track_uris = payload.get("track_uris")
    if not isinstance(track_uris, list):
        track_uris = []
    return AppliedAlbumPlan(
        job_id=str(payload.get("job_id", "")),
        dedupe_key=str(payload.get("dedupe_key", "")),
        title=str(payload.get("title", "")),
        description=str(payload.get("description", "")),
        track_uris=tuple(str(uri) for uri in track_uris),
        source=str(payload.get("source", "unknown")),
        rationale=payload.get("rationale") if isinstance(payload.get("rationale"), str) else None,
        applied_at=payload.get("applied_at") if isinstance(payload.get("applied_at"), str) else None,
    )
