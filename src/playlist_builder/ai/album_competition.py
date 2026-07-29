"""Album playlist AI competition gate: live Spotify search + one Codex job."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from playlist_builder.ai.album_planner import (
    AppliedAlbumPlan,
    build_album_copy_jobs,
    save_applied_plan,
    template_album_title,
)
from playlist_builder.playlist_templates import with_full_album_suffix
from playlist_builder.ai.queue import AiQueue
from playlist_builder.ai.research_competition import (
    ResearchCompetitionClient,
    _dedupe_queries,
    _search_query_bundle,
)
from playlist_builder.ai.schemas import (
    JOB_TYPE_ALBUM_COMPETITION,
    parse_album_competition_result,
)
from playlist_builder.persistence.models import CandidateStateRow


class AlbumCompetitionClient(Protocol):
    def search_playlists(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AlbumCompetitionJob:
    job_id: str
    dedupe_key: str
    artist_name: str
    project_title: str
    effective_status: str
    event_date: str | None
    starter_queries: tuple[str, ...]
    query_results: tuple[dict[str, Any], ...]
    track_uris: tuple[str, ...]
    proposed_title: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "type": JOB_TYPE_ALBUM_COMPETITION,
            "created_at": self.created_at,
            "context": {
                "dedupe_key": self.dedupe_key,
                "artist_name": self.artist_name,
                "project_title": self.project_title,
                "effective_status": self.effective_status,
                "event_date": self.event_date,
                "starter_queries": list(self.starter_queries),
                "query_results": list(self.query_results),
                "track_count": len(self.track_uris),
                "proposed_title": self.proposed_title,
            },
            "instructions": _album_competition_instructions(),
        }


@dataclass(frozen=True, slots=True)
class AppliedAlbumCompetition:
    job_id: str
    dedupe_key: str
    title: str
    competition_search_queries: tuple[str, ...]
    competition_verdict: str
    reasoning: str
    relevant_queries: tuple[str, ...]
    relevant_competitor_ids: tuple[str, ...]
    confidence: float | None
    description: str
    rationale: str | None
    revised_title: str | None
    revised_competition_search_queries: tuple[str, ...]
    applied_at: str
    source: str = "codex_cli"
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "dedupe_key": self.dedupe_key,
            "title": self.title,
            "competition_search_queries": list(self.competition_search_queries),
            "competition_verdict": self.competition_verdict,
            "reasoning": self.reasoning,
            "relevant_queries": list(self.relevant_queries),
            "relevant_competitor_ids": list(self.relevant_competitor_ids),
            "confidence": self.confidence,
            "description": self.description,
            "rationale": self.rationale,
            "revised_title": self.revised_title,
            "revised_competition_search_queries": list(self.revised_competition_search_queries),
            "applied_at": self.applied_at,
            "source": self.source,
            "attempt": self.attempt,
        }


def album_competition_job_id(dedupe_key: str, *, attempt: int = 1) -> str:
    suffix = "" if attempt <= 1 else f":retry{attempt}"
    return f"album_competition:{dedupe_key}{suffix}"


def starter_album_competition_queries(
    artist_name: str,
    project_title: str,
    event_date: str | None,
) -> tuple[str, ...]:
    artist = artist_name.strip()
    project = project_title.strip()
    year = event_date[:4] if event_date and len(event_date) >= 4 else ""
    variants = [
        f"{artist} {project}".strip(),
        f"{project} {artist}".strip(),
        f"{project} album".strip(),
        f"{artist} {project} playlist".strip(),
        f"{artist} {project} {year}".strip() if year else f"{project} {artist} songs".strip(),
        f"{project} songs".strip(),
    ]
    deduped = _dedupe_queries(variants)
    if len(deduped) >= 5:
        return deduped[:5]
    # Pad deterministically if artist/project collapse.
    while len(deduped) < 5:
        pad = f"{artist} {project} playlist {len(deduped) + 1}".strip()
        deduped = _dedupe_queries(list(deduped) + [pad])
    return deduped[:5]


def build_album_competition_job(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    *,
    client: AlbumCompetitionClient,
    catalog_client: Any,
    market: str | None = None,
    limit_per_query: int = 10,
    queries: tuple[str, ...] | None = None,
    proposed_title: str | None = None,
    attempt: int = 1,
    checked_at: datetime | None = None,
) -> AlbumCompetitionJob:
    copy_job = build_album_copy_jobs(
        candidate,
        payload,
        catalog_client,
        market=market,
        checked_at=checked_at,
    )
    starter = queries or starter_album_competition_queries(
        candidate.artist_name,
        candidate.project_title,
        candidate.event_date,
    )
    if len(starter) != 5:
        raise ValueError("album competition requires exactly 5 starter queries")

    query_results = tuple(
        _search_query_bundle(client, query, market=market, limit=limit_per_query)
        for query in starter
    )
    return AlbumCompetitionJob(
        job_id=album_competition_job_id(candidate.dedupe_key, attempt=attempt),
        dedupe_key=candidate.dedupe_key,
        artist_name=candidate.artist_name,
        project_title=candidate.project_title,
        effective_status=candidate.effective_status,
        event_date=candidate.event_date,
        starter_queries=starter,
        query_results=query_results,
        track_uris=copy_job.track_uris,
        proposed_title=proposed_title
        or template_album_title(
            candidate.artist_name,
            candidate.project_title,
            candidate.effective_status,
        ),
        created_at=(checked_at or datetime.now(timezone.utc)).isoformat(),
    )


def apply_album_competition_result(
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    track_uris: tuple[str, ...],
    source: str = "codex_cli",
    attempt: int = 1,
) -> AppliedAlbumCompetition:
    if str(job.get("type", "")) != JOB_TYPE_ALBUM_COMPETITION:
        raise ValueError(f"unsupported job type: {job.get('type')}")
    context = job.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("job missing context")
    parsed = parse_album_competition_result(result)
    return AppliedAlbumCompetition(
        job_id=str(job.get("id", "")),
        dedupe_key=str(context.get("dedupe_key", "")),
        title=parsed["title"],
        competition_search_queries=tuple(parsed["competition_search_queries"]),
        competition_verdict=parsed["competition_verdict"],
        reasoning=parsed["reasoning"],
        relevant_queries=tuple(parsed["relevant_queries"]),
        relevant_competitor_ids=tuple(parsed["relevant_competitor_ids"]),
        confidence=parsed["confidence"],
        description=parsed["description"],
        rationale=parsed.get("rationale"),
        revised_title=parsed["revised_title"],
        revised_competition_search_queries=tuple(parsed["revised_competition_search_queries"]),
        applied_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        attempt=attempt,
    )


def persist_album_competition_result(
    store: Any,
    applied_result: AppliedAlbumCompetition,
    *,
    queue: AiQueue | None = None,
    track_uris: tuple[str, ...] | None = None,
) -> AppliedAlbumPlan | None:
    """Persist competition evaluation; on publish also write applied album plan."""

    from datetime import date

    from playlist_builder.persistence.validation_record import ValidationPersistenceRecord

    candidate = store.get_candidate_state(applied_result.dedupe_key)
    payload = store.get_latest_payload(applied_result.dedupe_key) or {}
    if candidate is None:
        raise ValueError(f"missing candidate state for {applied_result.dedupe_key}")

    payload = dict(payload)
    payload["competition_evaluation"] = applied_result.to_dict()

    publish_pending = candidate.publish_pending
    if applied_result.competition_verdict == "skip":
        publish_pending = False

    record = ValidationPersistenceRecord(
        dedupe_key=candidate.dedupe_key,
        source=candidate.source,
        source_event_id=candidate.source_event_id,
        event_type=candidate.event_type,
        artist_name=candidate.artist_name,
        project_title=candidate.project_title,
        event_date=date.fromisoformat(candidate.event_date) if candidate.event_date else None,
        spotify_artist_id=candidate.spotify_artist_id,
        effective_status=candidate.effective_status,
        artist_value_tier=candidate.artist_value_tier,
        prerelease_uri=candidate.prerelease_uri,
        resolved_track_uris=tuple(candidate.resolved_track_uris),
        score_action=candidate.score_action,
        checked_at=datetime.now(timezone.utc),
        next_check_at=candidate.next_check_at,
        publish_pending=publish_pending,
        payload=payload,
    )
    store.record_validation(record)

    if (
        applied_result.competition_verdict == "publish"
        and queue is not None
        and track_uris is not None
    ):
        plan = AppliedAlbumPlan(
            job_id=applied_result.job_id,
            dedupe_key=applied_result.dedupe_key,
            title=with_full_album_suffix(applied_result.title),
            description=applied_result.description[:300],
            track_uris=track_uris,
            source=applied_result.source,
            rationale=applied_result.rationale,
            applied_at=applied_result.applied_at,
        )
        save_applied_plan(queue, plan)
        return plan
    return None


def generate_and_persist_album_competition(
    job: AlbumCompetitionJob,
    store: Any,
    *,
    runner: Any,
    queue: AiQueue,
    attempt: int = 1,
) -> AppliedAlbumCompetition:
    from playlist_builder.ai.codex_cli import (
        build_album_competition_prompt,
        schema_path,
        wrap_ok_output,
    )

    output = runner.run_json(
        prompt=build_album_competition_prompt(job.to_dict()),
        schema_file=schema_path("album_competition.json"),
    )
    applied = apply_album_competition_result(
        job.to_dict(),
        wrap_ok_output(output),
        track_uris=job.track_uris,
        source="codex_cli",
        attempt=attempt,
    )
    persist_album_competition_result(
        store,
        applied,
        queue=queue,
        track_uris=job.track_uris if applied.competition_verdict == "publish" else None,
    )
    return applied


def generate_album_competition_with_retitle(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    store: Any,
    *,
    client: AlbumCompetitionClient | ResearchCompetitionClient,
    catalog_client: Any,
    runner: Any,
    queue: AiQueue,
    market: str | None = None,
    sleep_fn: Any | None = None,
    stretch_seconds: float = 2.0,
) -> AppliedAlbumCompetition:
    """One Codex competition pass with a single retitle retry (live re-search)."""

    job = build_album_competition_job(
        candidate,
        payload,
        client=client,
        catalog_client=catalog_client,
        market=market,
        attempt=1,
    )
    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)
    applied = generate_and_persist_album_competition(
        job,
        store,
        runner=runner,
        queue=queue,
        attempt=1,
    )
    if applied.competition_verdict != "retitle":
        return applied

    revised_queries = applied.revised_competition_search_queries
    revised_title = applied.revised_title
    if not revised_title or len(revised_queries) != 5:
        # Invalid retitle payload → treat as skip.
        skipped = AppliedAlbumCompetition(
            job_id=applied.job_id,
            dedupe_key=applied.dedupe_key,
            title=applied.title,
            competition_search_queries=applied.competition_search_queries,
            competition_verdict="skip",
            reasoning=applied.reasoning + " (retitle missing revised title/queries; skipped)",
            relevant_queries=applied.relevant_queries,
            relevant_competitor_ids=applied.relevant_competitor_ids,
            confidence=applied.confidence,
            description=applied.description,
            rationale=applied.rationale,
            revised_title=revised_title,
            revised_competition_search_queries=revised_queries,
            applied_at=datetime.now(timezone.utc).isoformat(),
            source=applied.source,
            attempt=1,
        )
        persist_album_competition_result(store, skipped, queue=queue, track_uris=None)
        return skipped

    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)

    retry_job = build_album_competition_job(
        candidate,
        payload,
        client=client,
        catalog_client=catalog_client,
        market=market,
        queries=revised_queries,
        proposed_title=revised_title,
        attempt=2,
    )
    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)
    retry = generate_and_persist_album_competition(
        retry_job,
        store,
        runner=runner,
        queue=queue,
        attempt=2,
    )
    if retry.competition_verdict == "retitle":
        skipped = AppliedAlbumCompetition(
            job_id=retry.job_id,
            dedupe_key=retry.dedupe_key,
            title=retry.title,
            competition_search_queries=retry.competition_search_queries,
            competition_verdict="skip",
            reasoning=retry.reasoning + " (second retitle not allowed; skipped)",
            relevant_queries=retry.relevant_queries,
            relevant_competitor_ids=retry.relevant_competitor_ids,
            confidence=retry.confidence,
            description=retry.description,
            rationale=retry.rationale,
            revised_title=retry.revised_title,
            revised_competition_search_queries=retry.revised_competition_search_queries,
            applied_at=datetime.now(timezone.utc).isoformat(),
            source=retry.source,
            attempt=2,
        )
        persist_album_competition_result(store, skipped, queue=queue, track_uris=None)
        return skipped
    return retry


def _album_competition_instructions() -> str:
    return (
        "Evaluate Spotify playlist competition for an album playlist and produce SEO copy.\n"
        "You receive starter search queries and LIVE Spotify search results. Do NOT invent competitors.\n"
        "Return one final SEO title, exactly 5 competition_search_queries, a verdict, and a description.\n"
        "Rules:\n"
        "- Treat raw Spotify search as noisy; only count truly relevant competing playlists.\n"
        "- Large follower counts matter only if the playlist is genuinely relevant.\n"
        "- Verdict publish: title is viable and competition is acceptable.\n"
        "- Verdict skip: keyword space too crowded / not worth publishing.\n"
        "- Verdict retitle: propose revised_title + revised_competition_search_queries (exactly 5).\n"
        "- Titles (including revised_title) must look like real public Spotify playlist names: "
        "short, searchable, listener-facing, end with ` (full album)`, "
        "no Research/Opportunity/meta wording, no full calendar dates.\n"
        "- If you cannot find a clean alternative title, return skip.\n"
        "- Description: listener-facing, ~300 chars max, artist+album for search, no marketing funnel language.\n"
        "Return strict JSON only matching the schema."
    )
