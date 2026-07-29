"""JSON shapes for album playlist copy jobs (tracks picked in Python)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


JOB_TYPE_ALBUM_COPY = "album_playlist_copy"
JOB_TYPE_ALBUM_COMPETITION = "album_playlist_competition"
JOB_TYPE_RESEARCH_COMPETITION = "research_playlist_competition"


@dataclass(frozen=True, slots=True)
class CatalogTrack:
    uri: str
    name: str
    album_name: str
    album_type: str
    popularity: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "uri": self.uri,
            "name": self.name,
            "album_name": self.album_name,
            "album_type": self.album_type,
        }
        if self.popularity is not None:
            payload["popularity"] = self.popularity
        return payload


@dataclass(frozen=True, slots=True)
class AnchorTrack:
    uri: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "name": self.name, "position_locked": True}


@dataclass(frozen=True, slots=True)
class AlbumPlanConstraints:
    min_tracks: int = 20
    max_tracks: int = 25

    def to_dict(self) -> dict[str, int]:
        return {"min_tracks": self.min_tracks, "max_tracks": self.max_tracks}


@dataclass(frozen=True, slots=True)
class AlbumCopyJob:
    job_id: str
    dedupe_key: str
    artist_name: str
    project_title: str
    effective_status: str
    event_date: str | None
    title: str
    track_uris: tuple[str, ...]
    anchor_track_names: tuple[str, ...]
    backfill_count: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "type": JOB_TYPE_ALBUM_COPY,
            "created_at": self.created_at,
            "context": {
                "dedupe_key": self.dedupe_key,
                "artist_name": self.artist_name,
                "project_title": self.project_title,
                "effective_status": self.effective_status,
                "event_date": self.event_date,
                "title": self.title,
                "track_uris": list(self.track_uris),
                "track_count": len(self.track_uris),
                "anchor_track_names": list(self.anchor_track_names),
                "backfill_count": self.backfill_count,
            },
            "instructions": _album_copy_instructions(),
        }


def _description_guidelines() -> str:
    return (
        "Description rules (Spotify public playlist, max ~300 chars):\n"
        "- Write for listeners and searchers, not marketers.\n"
        "- Say WHAT is on the playlist: new album singles first, then artist essentials.\n"
        "- Use artist + album name for search. Do NOT list individual track names.\n"
        "- Good: 'Released singles from Billie Eilish's HIT ME HARD AND SOFT so far, "
        "plus essentials from her earlier albums.'\n"
        "- Bad: 'maximize saves', 'conversion', 'recognition', 'engagement', or funnel language.\n"
        "- No hashtags, no emoji spam.\n"
    )


def _album_copy_instructions() -> str:
    return (
        "Write Spotify playlist copy only. Track order is ALREADY FINAL — do not pick or reorder songs.\n"
        + _description_guidelines()
        + "Return strict JSON only:\n"
        '{"description":"...","rationale":"..."}'
    )


def parse_album_copy_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "ok":
        raise ValueError(f"AI job failed: {payload.get('error', 'unknown')}")

    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("AI result missing output object")

    description = output.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("AI output missing description")

    rationale = output.get("rationale")
    return {
        "description": description.strip(),
        "rationale": str(rationale).strip() if rationale else None,
    }


def parse_research_competition_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "ok":
        raise ValueError(f"AI job failed: {payload.get('error', 'unknown')}")

    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("AI result missing output object")

    return _parse_competition_fields(output)


def parse_album_competition_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "ok":
        raise ValueError(f"AI job failed: {payload.get('error', 'unknown')}")

    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("AI result missing output object")

    parsed = _parse_competition_fields(output)

    title = output.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("AI output missing title")

    queries_raw = output.get("competition_search_queries")
    if not isinstance(queries_raw, list):
        raise ValueError("AI output missing competition_search_queries")
    queries = [str(item).strip() for item in queries_raw if str(item).strip()]
    if len(queries) != 5:
        raise ValueError("AI output must include exactly 5 competition_search_queries")

    description = output.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("AI output missing description")

    rationale = output.get("rationale")
    return {
        **parsed,
        "title": title.strip(),
        "competition_search_queries": queries,
        "description": description.strip(),
        "rationale": str(rationale).strip() if rationale else None,
    }


def _parse_competition_fields(output: Mapping[str, Any]) -> dict[str, Any]:
    verdict = str(output.get("competition_verdict", "")).strip().lower()
    if verdict not in {"publish", "skip", "retitle"}:
        raise ValueError("AI output missing valid competition_verdict")

    reasoning = output.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("AI output missing reasoning")

    relevant_queries_raw = output.get("relevant_queries")
    relevant_queries = []
    if isinstance(relevant_queries_raw, list):
        relevant_queries = [str(item).strip() for item in relevant_queries_raw if str(item).strip()]

    competitor_ids_raw = output.get("relevant_competitor_ids")
    relevant_competitor_ids = []
    if isinstance(competitor_ids_raw, list):
        relevant_competitor_ids = [str(item).strip() for item in competitor_ids_raw if str(item).strip()]

    confidence_value = output.get("confidence")
    confidence = None
    if confidence_value is not None:
        confidence = float(confidence_value)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    revised_title = output.get("revised_title")
    if revised_title is not None:
        revised_title = str(revised_title).strip() or None

    revised_queries_raw = output.get("revised_competition_search_queries")
    revised_queries = []
    if isinstance(revised_queries_raw, list):
        revised_queries = [str(item).strip() for item in revised_queries_raw if str(item).strip()]

    return {
        "competition_verdict": verdict,
        "reasoning": reasoning.strip(),
        "relevant_queries": relevant_queries,
        "relevant_competitor_ids": relevant_competitor_ids,
        "confidence": confidence,
        "revised_title": revised_title,
        "revised_competition_search_queries": revised_queries,
    }
