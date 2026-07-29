"""Normalized event model for playlist-building opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, TypeAlias


JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


class EventType(str, Enum):
    """Supported opportunity categories."""

    ALBUM_DROP = "album_drop"
    UPCOMING_ALBUM_CANDIDATE = "upcoming_album_candidate"
    PRE_RELEASE_ALBUM_OPPORTUNITY = "pre_release_album_opportunity"
    FESTIVAL = "festival"
    TOUR = "tour"
    MOMENT = "moment"


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Common shape produced by source watchers before scoring or publishing."""

    event_type: EventType
    source: str
    title: str
    artist_names: tuple[str, ...] = ()
    event_date: date | None = None
    confidence: float = 1.0
    raw_payload: Mapping[str, JSONValue] = field(default_factory=dict)
    candidate_search_terms: tuple[str, ...] = ()
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        event_type = EventType(self.event_type)
        artist_names = _clean_tuple(self.artist_names, "artist_names")
        search_terms = _clean_tuple(
            self.candidate_search_terms, "candidate_search_terms"
        )
        raw_payload = dict(self.raw_payload)

        if not self.source.strip():
            raise ValueError("source must not be empty")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if isinstance(self.event_date, datetime):
            raise TypeError("event_date must be a date, not a datetime")
        if self.detected_at.tzinfo is None:
            raise ValueError("detected_at must be timezone-aware")
        if self.source_event_id is not None and not self.source_event_id.strip():
            raise ValueError("source_event_id must not be blank")

        _validate_json_value(raw_payload, "raw_payload")

        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "artist_names", artist_names)
        object.__setattr__(self, "candidate_search_terms", search_terms)
        object.__setattr__(self, "raw_payload", raw_payload)
        if self.source_event_id is not None:
            object.__setattr__(self, "source_event_id", self.source_event_id.strip())

    @property
    def dedupe_key(self) -> str:
        """Stable key for later storage and duplicate checks."""

        if self.source_event_id:
            return f"{self.source}:{self.event_type.value}:{self.source_event_id}"

        event_date = self.event_date.isoformat() if self.event_date else "undated"
        artists = ",".join(name.lower() for name in self.artist_names) or "unknown"
        return f"{self.source}:{self.event_type.value}:{event_date}:{artists}:{self.title.lower()}"

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a JSON-compatible representation."""

        return {
            "event_type": self.event_type.value,
            "source": self.source,
            "title": self.title,
            "artist_names": list(self.artist_names),
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "confidence": self.confidence,
            "raw_payload": dict(self.raw_payload),
            "candidate_search_terms": list(self.candidate_search_terms),
            "detected_at": self.detected_at.isoformat(),
            "source_event_id": self.source_event_id,
            "dedupe_key": self.dedupe_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizedEvent":
        """Build an event from a JSON-like mapping."""

        event_date = data.get("event_date")
        detected_at = data.get("detected_at")

        return cls(
            event_type=EventType(data["event_type"]),
            source=str(data["source"]),
            title=str(data["title"]),
            artist_names=tuple(data.get("artist_names") or ()),
            event_date=date.fromisoformat(event_date) if event_date else None,
            confidence=float(data.get("confidence", 1.0)),
            raw_payload=data.get("raw_payload") or {},
            candidate_search_terms=tuple(data.get("candidate_search_terms") or ()),
            detected_at=(
                datetime.fromisoformat(detected_at)
                if detected_at
                else datetime.now(timezone.utc)
            ),
            source_event_id=data.get("source_event_id"),
        )


def _clean_tuple(values: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    cleaned = tuple(value.strip() for value in values)
    if any(not value for value in cleaned):
        raise ValueError(f"{field_name} must not contain blank values")
    return cleaned


def _validate_json_value(value: JSONValue, path: str) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return

    raise TypeError(f"{path} must be JSON-compatible")
