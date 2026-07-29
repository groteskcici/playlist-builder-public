"""Parse research-agent festival JSON results."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from playlist_builder.events import EventType, NormalizedEvent

MAX_FESTIVAL_CANDIDATES = 10


@dataclass(frozen=True, slots=True)
class FestivalResearchSource:
    title: str
    url: str
    source_type: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FestivalResearchSource":
        title = _required_str(data, "title")
        url = _required_str(data, "url")
        source_type = _required_str(data, "source_type")
        if source_type not in {"official", "ticketing", "publication", "venue", "promoter"}:
            raise ValueError(f"unsupported source_type: {source_type}")
        return cls(title=title, url=url, source_type=source_type)

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
        }


@dataclass(frozen=True, slots=True)
class FestivalResearchCandidate:
    event_name: str
    country: str
    city_or_region: str
    date_start: date
    date_end: date
    primary_artists: tuple[str, ...]
    notable_supporting_artists: tuple[str, ...]
    genre_focus: tuple[str, ...]
    why_it_matters: str
    playlist_angle: str
    suggested_playlist_title: str
    competition_search_queries: tuple[str, ...]
    confidence: float
    sources: tuple[FestivalResearchSource, ...]
    raw_payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FestivalResearchCandidate":
        event_type = _required_str(data, "event_type")
        if event_type != "festival":
            raise ValueError(f"unsupported event_type: {event_type}")

        date_start = date.fromisoformat(_required_str(data, "date_start"))
        date_end = date.fromisoformat(_required_str(data, "date_end"))
        if date_end < date_start:
            raise ValueError("date_end must not be before date_start")

        confidence = float(data.get("confidence", 0.0))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        sources = tuple(FestivalResearchSource.from_dict(item) for item in _required_list(data, "sources"))
        if not sources:
            raise ValueError("candidate must include at least one source")

        competition_search_queries = _competition_queries(
            data.get("competition_search_queries"),
            event_name=_required_str(data, "event_name"),
            suggested_playlist_title=_required_str(data, "suggested_playlist_title"),
            primary_artists=_str_tuple(data.get("primary_artists"), "primary_artists"),
        )

        return cls(
            event_name=_required_str(data, "event_name"),
            country=_required_str(data, "country"),
            city_or_region=_required_str(data, "city_or_region"),
            date_start=date_start,
            date_end=date_end,
            primary_artists=_str_tuple(data.get("primary_artists"), "primary_artists"),
            notable_supporting_artists=_str_tuple(
                data.get("notable_supporting_artists"),
                "notable_supporting_artists",
            ),
            genre_focus=_str_tuple(data.get("genre_focus"), "genre_focus"),
            why_it_matters=_required_str(data, "why_it_matters"),
            playlist_angle=_required_str(data, "playlist_angle"),
            suggested_playlist_title=_required_str(data, "suggested_playlist_title"),
            competition_search_queries=competition_search_queries,
            confidence=confidence,
            sources=sources,
            raw_payload=dict(data),
        )

    def to_event(self, *, detected_at: datetime | None = None) -> NormalizedEvent:
        artists = tuple(dict.fromkeys((*self.primary_artists, *self.notable_supporting_artists)))
        candidate_search_terms = (
            self.competition_search_queries
            if self.competition_search_queries
            else (
                self.event_name,
                self.suggested_playlist_title,
            )
        )
        return NormalizedEvent(
            event_type=EventType.FESTIVAL,
            source="festival_research",
            title=self.event_name,
            artist_names=artists,
            event_date=self.date_start,
            confidence=self.confidence,
            raw_payload=self.to_dict(),
            candidate_search_terms=candidate_search_terms,
            detected_at=detected_at or datetime.now(timezone.utc),
            source_event_id=f"{self.date_start.isoformat()}:{_slug(self.event_name)}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_name": self.event_name,
            "event_type": "festival",
            "country": self.country,
            "city_or_region": self.city_or_region,
            "date_start": self.date_start.isoformat(),
            "date_end": self.date_end.isoformat(),
            "primary_artists": list(self.primary_artists),
            "notable_supporting_artists": list(self.notable_supporting_artists),
            "genre_focus": list(self.genre_focus),
            "why_it_matters": self.why_it_matters,
            "playlist_angle": self.playlist_angle,
            "suggested_playlist_title": self.suggested_playlist_title,
            "competition_search_queries": list(self.competition_search_queries),
            "confidence": self.confidence,
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True, slots=True)
class FestivalResearchResult:
    run_date: date
    candidates: tuple[FestivalResearchCandidate, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FestivalResearchResult":
        run_date = date.fromisoformat(_required_str(data, "run_date"))
        candidates = tuple(
            FestivalResearchCandidate.from_dict(item)
            for item in _required_list(data, "candidates")
        )
        if len(candidates) > MAX_FESTIVAL_CANDIDATES:
            raise ValueError(
                f"festival research must contain at most {MAX_FESTIVAL_CANDIDATES} candidates"
            )
        return cls(run_date=run_date, candidates=candidates)

    def to_events(self) -> tuple[NormalizedEvent, ...]:
        detected_at = datetime.now(timezone.utc)
        return tuple(candidate.to_event(detected_at=detected_at) for candidate in self.candidates)


def load_festival_research(path: str | Path) -> FestivalResearchResult:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("festival research file must contain a JSON object")
    return FestivalResearchResult.from_dict(payload)


def _required_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required string field: {key}")
    return value.strip()


def _required_list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"missing required list field: {key}")
    return value


def _str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"missing required list field: {key}")
    cleaned = tuple(str(item).strip() for item in value if str(item).strip())
    if not cleaned:
        raise ValueError(f"{key} must not be empty")
    return cleaned


def _optional_exact_str_tuple(value: Any, key: str, *, expected_length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list when provided")
    cleaned = tuple(str(item).strip() for item in value if str(item).strip())
    if len(cleaned) != expected_length:
        raise ValueError(f"{key} must contain exactly {expected_length} entries when provided")
    return cleaned


def _competition_queries(
    value: Any,
    *,
    event_name: str,
    suggested_playlist_title: str,
    primary_artists: tuple[str, ...],
) -> tuple[str, ...]:
    if value is not None:
        return _optional_exact_str_tuple(value, "competition_search_queries", expected_length=5)

    base_name = event_name.strip()
    bare_name = re.sub(r"\b20\d{2}\b", "", base_name).replace("  ", " ").strip(" -") or base_name
    first_artist = primary_artists[0] if primary_artists else base_name
    return (
        suggested_playlist_title,
        base_name,
        bare_name,
        f"{bare_name} lineup",
        f"{bare_name} {first_artist}",
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
