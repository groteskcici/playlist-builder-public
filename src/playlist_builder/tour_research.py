"""Parse research-agent tour JSON results."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from playlist_builder.events import EventType, NormalizedEvent

MAX_TOUR_CANDIDATES = 5
_ALLOWED_SOURCE_TYPES = {"official", "ticketing", "publication", "venue", "promoter"}


@dataclass(frozen=True, slots=True)
class TourResearchSource:
    title: str
    url: str
    source_type: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TourResearchSource":
        title = _required_str(data, "title")
        url = _required_str(data, "url")
        source_type = _required_str(data, "source_type")
        if source_type not in _ALLOWED_SOURCE_TYPES:
            raise ValueError(f"unsupported source_type: {source_type}")
        return cls(title=title, url=url, source_type=source_type)

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
        }


@dataclass(frozen=True, slots=True)
class TourResearchCandidate:
    event_name: str
    tour_name: str
    headliner: str
    country: str
    city_or_region: str
    date_start: date
    date_end: date
    supporting_artists: tuple[str, ...]
    genre_focus: tuple[str, ...]
    tour_type: tuple[str, ...]
    why_it_matters: str
    playlist_angle: str
    suggested_playlist_title: str
    competition_search_queries: tuple[str, ...]
    confidence: float
    sources: tuple[TourResearchSource, ...]
    raw_payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TourResearchCandidate":
        event_type = _required_str(data, "event_type")
        if event_type != "tour":
            raise ValueError(f"unsupported event_type: {event_type}")

        date_start = date.fromisoformat(_required_str(data, "date_start"))
        date_end = date.fromisoformat(_required_str(data, "date_end"))
        if date_end < date_start:
            raise ValueError("date_end must not be before date_start")

        confidence = float(data.get("confidence", 0.0))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        sources = tuple(TourResearchSource.from_dict(item) for item in _required_list(data, "sources"))
        if not sources:
            raise ValueError("candidate must include at least one source")

        event_name = _required_str(data, "event_name")
        tour_name = _required_str(data, "tour_name")
        headliner = _required_str(data, "headliner")
        suggested_playlist_title = _required_str(data, "suggested_playlist_title")
        competition_search_queries = _competition_queries(
            data.get("competition_search_queries"),
            event_name=event_name,
            tour_name=tour_name,
            headliner=headliner,
            suggested_playlist_title=suggested_playlist_title,
        )

        return cls(
            event_name=_required_str(data, "event_name"),
            tour_name=tour_name,
            headliner=headliner,
            country=_required_str(data, "country"),
            city_or_region=_required_str(data, "city_or_region"),
            date_start=date_start,
            date_end=date_end,
            supporting_artists=_optional_str_tuple(data.get("supporting_artists"), "supporting_artists"),
            genre_focus=_str_tuple(data.get("genre_focus"), "genre_focus"),
            tour_type=_str_tuple(data.get("tour_type"), "tour_type"),
            why_it_matters=_required_str(data, "why_it_matters"),
            playlist_angle=_required_str(data, "playlist_angle"),
            suggested_playlist_title=suggested_playlist_title,
            competition_search_queries=competition_search_queries,
            confidence=confidence,
            sources=sources,
            raw_payload=dict(data),
        )

    def to_event(self, *, detected_at: datetime | None = None) -> NormalizedEvent:
        artists = tuple(dict.fromkeys((self.headliner, *self.supporting_artists)))
        candidate_search_terms = (
            self.competition_search_queries
            if self.competition_search_queries
            else (
                self.tour_name,
                self.headliner,
                self.suggested_playlist_title,
            )
        )
        return NormalizedEvent(
            event_type=EventType.TOUR,
            source="tour_research",
            title=self.event_name,
            artist_names=artists,
            event_date=self.date_start,
            confidence=self.confidence,
            raw_payload=self.to_dict(),
            candidate_search_terms=candidate_search_terms,
            detected_at=detected_at or datetime.now(timezone.utc),
            source_event_id=(
                f"{self.date_start.isoformat()}:"
                f"{_slug(self.headliner)}:{_slug(self.tour_name)}"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_name": self.event_name,
            "event_type": "tour",
            "tour_name": self.tour_name,
            "headliner": self.headliner,
            "country": self.country,
            "city_or_region": self.city_or_region,
            "date_start": self.date_start.isoformat(),
            "date_end": self.date_end.isoformat(),
            "supporting_artists": list(self.supporting_artists),
            "genre_focus": list(self.genre_focus),
            "tour_type": list(self.tour_type),
            "why_it_matters": self.why_it_matters,
            "playlist_angle": self.playlist_angle,
            "suggested_playlist_title": self.suggested_playlist_title,
            "competition_search_queries": list(self.competition_search_queries),
            "confidence": self.confidence,
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True, slots=True)
class TourResearchResult:
    run_date: date
    candidates: tuple[TourResearchCandidate, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TourResearchResult":
        run_date = date.fromisoformat(_required_str(data, "run_date"))
        candidates = tuple(
            TourResearchCandidate.from_dict(item)
            for item in _required_list(data, "candidates")
        )
        if len(candidates) > MAX_TOUR_CANDIDATES:
            raise ValueError(
                f"tour research must contain at most {MAX_TOUR_CANDIDATES} candidates"
            )
        return cls(run_date=run_date, candidates=candidates)

    def to_events(self) -> tuple[NormalizedEvent, ...]:
        detected_at = datetime.now(timezone.utc)
        return tuple(candidate.to_event(detected_at=detected_at) for candidate in self.candidates)


def load_tour_research(path: str | Path) -> TourResearchResult:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("tour research file must contain a JSON object")
    return TourResearchResult.from_dict(payload)


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


def _optional_str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list when provided")
    return tuple(str(item).strip() for item in value if str(item).strip())


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
    tour_name: str,
    headliner: str,
    suggested_playlist_title: str,
) -> tuple[str, ...]:
    if value is not None:
        return _optional_exact_str_tuple(value, "competition_search_queries", expected_length=5)

    return (
        suggested_playlist_title,
        tour_name,
        f"{headliner} {tour_name}",
        f"{headliner} tour",
        f"{headliner} setlist {tour_name}",
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
