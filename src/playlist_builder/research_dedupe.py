"""Dedupe freshly generated research candidates against the existing DB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from playlist_builder.festival_research import FestivalResearchResult
from playlist_builder.moment_research import MomentResearchResult
from playlist_builder.tour_research import TourResearchResult

_RESEARCH_SOURCES = frozenset({"festival_research", "tour_research", "moment_research"})
_RESEARCH_EVENT_TYPES = frozenset({"festival", "tour", "moment"})
_YEAR_RE = re.compile(r"\b20\d{2}\b")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ResearchDedupeStore(Protocol):
    def list_candidates(
        self,
        *,
        limit: int = 50,
        publish_pending_only: bool = False,
    ) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class ExistingResearchIndex:
    dedupe_keys: frozenset[str]
    name_keys: frozenset[str]

    def __contains__(self, item: object) -> bool:  # pragma: no cover - convenience
        if not isinstance(item, str):
            return False
        return item in self.dedupe_keys or item in self.name_keys


@dataclass(frozen=True, slots=True)
class ResearchDedupeResult:
    payload: dict[str, Any]
    kept_count: int
    dropped_count: int
    dropped: tuple[dict[str, str], ...]


def normalize_research_name(value: str) -> str:
    """Normalize event names for duplicate detection (case/year/punctuation insensitive)."""

    text = _YEAR_RE.sub(" ", value.casefold())
    slug = _SLUG_RE.sub("-", text).strip("-")
    return slug


def load_existing_research_index(
    store: ResearchDedupeStore,
    *,
    limit: int = 2000,
) -> ExistingResearchIndex:
    dedupe_keys: set[str] = set()
    name_keys: set[str] = set()
    for row in store.list_candidates(limit=limit, publish_pending_only=False):
        source = str(getattr(row, "source", "") or "")
        event_type = str(getattr(row, "event_type", "") or "")
        if source not in _RESEARCH_SOURCES and event_type not in _RESEARCH_EVENT_TYPES:
            continue
        key = str(getattr(row, "dedupe_key", "") or "").strip()
        if key:
            dedupe_keys.add(key)
        title = str(getattr(row, "project_title", "") or "").strip()
        if title:
            name_keys.add(normalize_research_name(title))
        source_event_id = getattr(row, "source_event_id", None)
        if isinstance(source_event_id, str) and source_event_id.strip():
            # source_event_id is usually "<date>:<slug>" or similar
            slug_part = source_event_id.rsplit(":", 1)[-1]
            name_keys.add(normalize_research_name(slug_part))
    return ExistingResearchIndex(
        dedupe_keys=frozenset(dedupe_keys),
        name_keys=frozenset(name_keys),
    )


def extend_research_index_from_payload(
    existing: ExistingResearchIndex,
    payload: Mapping[str, Any],
) -> ExistingResearchIndex:
    """Fold kept candidate names into the in-memory exclusion index."""

    name_keys = set(existing.name_keys)
    for item in payload.get("candidates", []):
        if not isinstance(item, Mapping):
            continue
        event_name = str(item.get("event_name") or "").strip()
        tour_name = str(item.get("tour_name") or "").strip()
        if event_name:
            name_keys.add(normalize_research_name(event_name))
        if tour_name:
            name_keys.add(normalize_research_name(tour_name))
    return ExistingResearchIndex(
        dedupe_keys=existing.dedupe_keys,
        name_keys=frozenset(name_keys),
    )


def filter_research_payload(
    kind: str,
    payload: Mapping[str, Any],
    existing: ExistingResearchIndex,
) -> ResearchDedupeResult:
    """Drop candidates that already exist by dedupe_key or normalized event name."""

    data = dict(payload)
    if kind == "festival":
        result = FestivalResearchResult.from_dict(data)
        kept_candidates = []
        dropped: list[dict[str, str]] = []
        for candidate in result.candidates:
            event = candidate.to_event()
            name_key = normalize_research_name(candidate.event_name)
            if event.dedupe_key in existing.dedupe_keys or name_key in existing.name_keys:
                dropped.append(
                    {
                        "event_name": candidate.event_name,
                        "dedupe_key": event.dedupe_key,
                        "reason": "already_in_db",
                    }
                )
                continue
            kept_candidates.append(candidate)
        data["candidates"] = [item.to_dict() for item in kept_candidates]
    elif kind == "tour":
        result = TourResearchResult.from_dict(data)
        kept_candidates = []
        dropped = []
        for candidate in result.candidates:
            event = candidate.to_event()
            name_key = normalize_research_name(candidate.tour_name or candidate.event_name)
            if event.dedupe_key in existing.dedupe_keys or name_key in existing.name_keys:
                dropped.append(
                    {
                        "event_name": candidate.event_name,
                        "dedupe_key": event.dedupe_key,
                        "reason": "already_in_db",
                    }
                )
                continue
            kept_candidates.append(candidate)
        data["candidates"] = [item.to_dict() for item in kept_candidates]
    elif kind == "moment":
        result = MomentResearchResult.from_dict(data)
        kept_candidates = []
        dropped = []
        for candidate in result.candidates:
            event = candidate.to_event()
            name_key = normalize_research_name(candidate.event_name)
            if event.dedupe_key in existing.dedupe_keys or name_key in existing.name_keys:
                dropped.append(
                    {
                        "event_name": candidate.event_name,
                        "dedupe_key": event.dedupe_key,
                        "reason": "already_in_db",
                    }
                )
                continue
            kept_candidates.append(candidate)
        data["candidates"] = [item.to_dict() for item in kept_candidates]
    else:
        raise ValueError(f"unsupported research kind: {kind}")

    kept_count = len(data["candidates"])
    return ResearchDedupeResult(
        payload=data,
        kept_count=kept_count,
        dropped_count=len(dropped),
        dropped=tuple(dropped),
    )
