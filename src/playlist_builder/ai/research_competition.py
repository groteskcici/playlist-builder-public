"""AI-assisted competition review for research playlist titles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any, Mapping, Protocol

from playlist_builder.ai.schemas import (
    JOB_TYPE_RESEARCH_COMPETITION,
    parse_research_competition_result,
)
from playlist_builder.persistence.models import CandidateStateRow


class MissingResearchCompetitionQueriesError(ValueError):
    """Raised when a research candidate is missing AI-generated competition queries."""


class ResearchCompetitionClient(Protocol):
    def search_playlists(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]: ...


class SpotifyBearerCompetitionClient:
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token.strip()
        if not self._access_token:
            raise ValueError("access_token must not be empty")

    def search_playlists(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {"q": query, "type": "playlist", "limit": min(limit, 50)}
        if market:
            params["market"] = market
        response = self._get_json(f"/search?{urlencode(params)}")
        items = response.get("playlists", {}).get("items", [])
        return items if isinstance(items, list) else []

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]:
        return self._get_json(f"/playlists/{playlist_id}")

    def _get_json(self, path: str) -> Mapping[str, Any]:
        req = Request(
            f"https://api.spotify.com/v1{path}",
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode())
        return payload if isinstance(payload, Mapping) else {}


@dataclass(frozen=True, slots=True)
class ResearchCompetitionJob:
    job_id: str
    dedupe_key: str
    candidate_source: str
    event_type: str
    artist_name: str
    project_title: str
    event_date: str | None
    title: str
    competition_search_queries: tuple[str, ...]
    query_results: tuple[dict[str, Any], ...]
    created_at: str
    canonical_event_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "type": JOB_TYPE_RESEARCH_COMPETITION,
            "created_at": self.created_at,
            "context": {
                "dedupe_key": self.dedupe_key,
                "candidate_source": self.candidate_source,
                "event_type": self.event_type,
                "artist_name": self.artist_name,
                "project_title": self.project_title,
                "event_date": self.event_date,
                "canonical_event_name": self.canonical_event_name,
                "title": self.title,
                "competition_search_queries": list(self.competition_search_queries),
                "query_results": list(self.query_results),
            },
            "instructions": _research_competition_instructions(self.event_type),
        }


@dataclass(frozen=True, slots=True)
class AppliedResearchCompetition:
    job_id: str
    dedupe_key: str
    title: str
    competition_search_queries: tuple[str, ...]
    competition_verdict: str
    reasoning: str
    relevant_queries: tuple[str, ...]
    relevant_competitor_ids: tuple[str, ...]
    confidence: float | None
    revised_title: str | None
    revised_competition_search_queries: tuple[str, ...]
    applied_at: str
    source: str = "codex_cli"

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
            "revised_title": self.revised_title,
            "revised_competition_search_queries": list(self.revised_competition_search_queries),
            "applied_at": self.applied_at,
            "source": self.source,
        }


def research_competition_job_id(dedupe_key: str) -> str:
    return f"research_competition:{dedupe_key}"


def title_and_queries_from_payload(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]]:
    event = payload.get("event")
    raw = event.get("raw_payload") if isinstance(event, Mapping) else None
    raw = raw if isinstance(raw, Mapping) else None

    title = _raw_text(raw, "suggested_playlist_title") or candidate.project_title.strip()
    queries = _string_list_from_mapping(raw, "competition_search_queries")
    deduped = _dedupe_queries(queries)
    if len(deduped) != 5:
        raise MissingResearchCompetitionQueriesError(
            "research candidate missing valid AI competition_search_queries (expected exactly 5)"
        )
    return title, deduped


def canonical_event_name_from_payload(payload: Mapping[str, Any]) -> str:
    """Best-verifiable festival/tour/event name from research raw payload."""

    event = payload.get("event")
    raw = event.get("raw_payload") if isinstance(event, Mapping) else None
    raw = raw if isinstance(raw, Mapping) else None
    for key in ("tour_name", "event_name"):
        text = _raw_text(raw, key)
        if text:
            return text
    return ""


def build_research_competition_job(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    *,
    client: ResearchCompetitionClient,
    market: str | None = None,
    limit_per_query: int = 10,
    checked_at: datetime | None = None,
) -> ResearchCompetitionJob:
    title, queries = title_and_queries_from_payload(candidate, payload)
    query_results = tuple(
        _search_query_bundle(client, query, market=market, limit=limit_per_query)
        for query in queries
    )
    return ResearchCompetitionJob(
        job_id=research_competition_job_id(candidate.dedupe_key),
        dedupe_key=candidate.dedupe_key,
        candidate_source=candidate.source,
        event_type=candidate.event_type,
        artist_name=candidate.artist_name,
        project_title=candidate.project_title,
        event_date=candidate.event_date,
        title=title,
        competition_search_queries=queries,
        query_results=query_results,
        created_at=(checked_at or datetime.now(timezone.utc)).isoformat(),
        canonical_event_name=canonical_event_name_from_payload(payload),
    )


def apply_research_competition_result(
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    source: str = "codex_cli",
) -> AppliedResearchCompetition:
    if str(job.get("type", "")) != JOB_TYPE_RESEARCH_COMPETITION:
        raise ValueError(f"unsupported job type: {job.get('type')}")
    context = job.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("job missing context")
    parsed = parse_research_competition_result(result)
    title = str(context.get("title", "")).strip()
    queries = tuple(
        str(item).strip()
        for item in context.get("competition_search_queries", [])
        if str(item).strip()
    )
    return AppliedResearchCompetition(
        job_id=str(job.get("id", "")),
        dedupe_key=str(context.get("dedupe_key", "")),
        title=title,
        competition_search_queries=queries,
        competition_verdict=parsed["competition_verdict"],
        reasoning=parsed["reasoning"],
        relevant_queries=tuple(parsed["relevant_queries"]),
        relevant_competitor_ids=tuple(parsed["relevant_competitor_ids"]),
        confidence=parsed["confidence"],
        revised_title=parsed["revised_title"],
        revised_competition_search_queries=tuple(parsed["revised_competition_search_queries"]),
        applied_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def persist_research_competition_result(
    store: Any,
    applied_result: AppliedResearchCompetition,
) -> None:
    """Write competition evaluation into candidate payload / publish_pending."""

    from datetime import date

    from playlist_builder.persistence.validation_record import ValidationPersistenceRecord

    candidate = store.get_candidate_state(applied_result.dedupe_key)
    payload = store.get_latest_payload(applied_result.dedupe_key) or {}
    if candidate is None:
        raise ValueError(f"missing candidate state for {applied_result.dedupe_key}")

    payload = dict(payload)
    payload["competition_evaluation"] = applied_result.to_dict()
    if applied_result.competition_verdict == "retitle" and applied_result.revised_title:
        event = payload.get("event")
        if isinstance(event, dict):
            event = dict(event)
            payload["event"] = event
            raw = event.get("raw_payload")
            if isinstance(raw, dict):
                raw = dict(raw)
                event["raw_payload"] = raw
                raw["suggested_playlist_title"] = applied_result.revised_title
                if applied_result.revised_competition_search_queries:
                    raw["competition_search_queries"] = list(
                        applied_result.revised_competition_search_queries
                    )

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


def generate_and_persist_research_competition(
    job: ResearchCompetitionJob,
    store: Any,
    *,
    runner: Any,
) -> AppliedResearchCompetition:
    """Call Codex for competition judgment and persist the evaluation."""

    from playlist_builder.ai.codex_cli import (
        build_research_competition_prompt,
        schema_path,
        wrap_ok_output,
    )

    output = runner.run_json(
        prompt=build_research_competition_prompt(job.to_dict()),
        schema_file=schema_path("research_competition.json"),
    )
    applied = apply_research_competition_result(
        job.to_dict(),
        wrap_ok_output(output),
        source="codex_cli",
    )
    persist_research_competition_result(store, applied)
    return applied


def generate_research_competition_with_retitle(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    store: Any,
    *,
    client: ResearchCompetitionClient,
    runner: Any,
    market: str | None = None,
    sleep_fn: Any | None = None,
    stretch_seconds: float = 2.0,
) -> AppliedResearchCompetition:
    """One Codex competition pass with a single retitle retry (live re-search)."""

    job = build_research_competition_job(
        candidate,
        payload,
        client=client,
        market=market,
    )
    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)
    applied = generate_and_persist_research_competition(job, store, runner=runner)
    if applied.competition_verdict != "retitle":
        return applied

    if not applied.revised_title or len(applied.revised_competition_search_queries) != 5:
        skipped = AppliedResearchCompetition(
            job_id=applied.job_id,
            dedupe_key=applied.dedupe_key,
            title=applied.title,
            competition_search_queries=applied.competition_search_queries,
            competition_verdict="skip",
            reasoning=applied.reasoning + " (retitle missing revised title/queries; skipped)",
            relevant_queries=applied.relevant_queries,
            relevant_competitor_ids=applied.relevant_competitor_ids,
            confidence=applied.confidence,
            revised_title=applied.revised_title,
            revised_competition_search_queries=applied.revised_competition_search_queries,
            applied_at=datetime.now(timezone.utc).isoformat(),
            source=applied.source,
        )
        persist_research_competition_result(store, skipped)
        return skipped

    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)

    refreshed_payload = store.get_latest_payload(candidate.dedupe_key) or payload
    retry_job = build_research_competition_job(
        candidate,
        refreshed_payload,
        client=client,
        market=market,
    )
    if sleep_fn and stretch_seconds > 0:
        sleep_fn(stretch_seconds)
    retry = generate_and_persist_research_competition(retry_job, store, runner=runner)
    if retry.competition_verdict == "retitle":
        skipped = AppliedResearchCompetition(
            job_id=retry.job_id,
            dedupe_key=retry.dedupe_key,
            title=retry.title,
            competition_search_queries=retry.competition_search_queries,
            competition_verdict="skip",
            reasoning=retry.reasoning + " (second retitle not allowed; skipped)",
            relevant_queries=retry.relevant_queries,
            relevant_competitor_ids=retry.relevant_competitor_ids,
            confidence=retry.confidence,
            revised_title=retry.revised_title,
            revised_competition_search_queries=retry.revised_competition_search_queries,
            applied_at=datetime.now(timezone.utc).isoformat(),
            source=retry.source,
        )
        persist_research_competition_result(store, skipped)
        return skipped
    return retry


def _search_query_bundle(
    client: ResearchCompetitionClient,
    query: str,
    *,
    market: str | None,
    limit: int,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for candidate in client.search_playlists(query, limit=limit, market=market):
        if not isinstance(candidate, Mapping):
            continue
        playlist_id = str(candidate.get("id") or "").strip()
        if not playlist_id:
            continue
        details = client.get_playlist(playlist_id)
        if not isinstance(details, Mapping):
            details = {}
        followers_payload = details.get("followers")
        followers = None
        if isinstance(followers_payload, Mapping):
            raw_total = followers_payload.get("total")
            if isinstance(raw_total, int):
                followers = raw_total
        owner_payload = details.get("owner")
        owner_name = None
        if isinstance(owner_payload, Mapping):
            raw_owner = owner_payload.get("display_name") or owner_payload.get("id")
            if isinstance(raw_owner, str) and raw_owner.strip():
                owner_name = raw_owner.strip()
        external_urls = details.get("external_urls")
        playlist_url = None
        if isinstance(external_urls, Mapping):
            raw_url = external_urls.get("spotify")
            if isinstance(raw_url, str) and raw_url.strip():
                playlist_url = raw_url.strip()
        matches.append(
            {
                "playlist_id": playlist_id,
                "name": str(candidate.get("name") or "").strip(),
                "followers": followers,
                "owner_name": owner_name,
                "playlist_url": playlist_url,
                "token_overlap": _token_overlap(query, str(candidate.get("name") or "")),
            }
        )
    return {"query": query, "results": matches}


def _token_overlap(query: str, name: str) -> float:
    query_tokens = set(_tokens(query))
    name_tokens = set(_tokens(name))
    if not query_tokens or not name_tokens:
        return 0.0
    return round(len(query_tokens & name_tokens) / len(query_tokens), 3)


def _tokens(text: str) -> list[str]:
    cleaned = []
    token = []
    for char in text.casefold():
        if char.isalnum():
            token.append(char)
            continue
        if token:
            cleaned.append("".join(token))
            token = []
    if token:
        cleaned.append("".join(token))
    return cleaned


def _dedupe_queries(queries: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        text = " ".join(query.split()).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return tuple(deduped)


def _string_list_from_mapping(raw_payload: Mapping[str, Any] | None, key: str) -> list[str]:
    if not isinstance(raw_payload, Mapping):
        return []
    value = raw_payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _raw_text(raw_payload: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(raw_payload, Mapping):
        return None
    value = raw_payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _research_competition_instructions(event_type: str = "") -> str:
    kind = str(event_type or "").strip().casefold()
    event_name_rules = ""
    if kind in {"festival", "tour"}:
        event_name_rules = (
            "- Festival/tour title rules (hard):\n"
            "  - Prefer the official/best-verifiable event or tour name from "
            "`canonical_event_name` (fallback: `project_title`).\n"
            "  - Retitle is ONLY for cleanup inside that real-name space: drop meta words, "
            "drop calendar dates, fix obvious wording, optionally add year/city for precision.\n"
            "  - Never invent a new angle just to dodge competition "
            "(no artist-first festival titles, no co-headline packages, no support-act mashups, "
            "no setlist/city niches).\n"
            "  - Bad retitles: `Jorja Smith at All Points East`, "
            "`Five Finger Death Punch & Lamb of God Tour`, "
            "`Gorillaz at Electric Picnic 2026`, `Artist Live in LA`.\n"
            "  - Good retitles (still real name): `All Points East 2026`, "
            "`20th Anniversary World Tour`, `the lucky me tour`, `Reading Festival 2026`.\n"
            "  - If the real festival/tour name keyword space is too crowded, return skip. "
            "Do not retitle away from the real name.\n"
        )
    return (
        "Evaluate Spotify playlist competition for a research playlist title. Do NOT publish anything.\n"
        "You will receive one proposed final title, five competition search queries, and Spotify search results for each query.\n"
        "Your job is to decide whether the competition is actually relevant and too strong. Ignore obvious noise and unrelated playlists.\n"
        "Rules:\n"
        "- Treat raw Spotify search as noisy; only count playlists that truly compete for the same keyword space.\n"
        "- Large follower counts matter only if the playlist is genuinely relevant.\n"
        "- Setlist playlists often count as real competitors if they dominate the same search phrase.\n"
        "- If the title is viable, return verdict publish.\n"
        "- If the keyword space is too crowded, return verdict skip.\n"
        "- If the title should be cleaned and retried, return verdict retitle with a revised_title and revised_competition_search_queries.\n"
        "- revised_title must look like a real public Spotify playlist name: short, searchable, listener-facing.\n"
        "- Never put process/meta wording in revised_title (Research, Lineup Research, Opportunity, Candidate, Batch, SEO, Keywords).\n"
        "- Never put full calendar dates or day+month stamps in revised_title "
        "(e.g. September 10, 2026 / 21 August / em-dash or colon date suffixes).\n"
        f"{event_name_rules}"
        "- If you cannot find a clean real-name alternative, return skip instead of inventing a trashy niche title.\n"
        "Return strict JSON only:\n"
        '{"competition_verdict":"publish|skip|retitle","reasoning":"...","confidence":0.0,'
        '"relevant_queries":["..."],"relevant_competitor_ids":["..."],'
        '"revised_title":"... or null","revised_competition_search_queries":["..."]}'
    )
