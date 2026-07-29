"""Import validated research JSON files into candidate persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from shutil import move
from typing import Any, Callable, Mapping

from playlist_builder.events import NormalizedEvent
from playlist_builder.festival_research import FestivalResearchResult, load_festival_research
from playlist_builder.moment_research import MomentResearchResult, load_moment_research
from playlist_builder.persistence.validation_record import ValidationPersistenceRecord
from playlist_builder.tour_research import TourResearchResult, load_tour_research

ResearchResult = FestivalResearchResult | TourResearchResult | MomentResearchResult
ResearchLoader = Callable[[str | Path], ResearchResult]


@dataclass(frozen=True, slots=True)
class ResearchImportItem:
    path: str
    dedupe_key: str
    event_type: str
    title: str
    changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "dedupe_key": self.dedupe_key,
            "event_type": self.event_type,
            "title": self.title,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class ResearchImportSummary:
    imported_file_count: int
    candidate_count: int
    changed_count: int
    archived_file_count: int
    archived_paths: tuple[str, ...]
    imported: tuple[ResearchImportItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "imported_file_count": self.imported_file_count,
            "candidate_count": self.candidate_count,
            "changed_count": self.changed_count,
            "archived_file_count": self.archived_file_count,
            "archived_paths": list(self.archived_paths),
            "imported": [item.to_dict() for item in self.imported],
        }


def import_research_files(
    paths: list[str | Path],
    *,
    store,
    kind: str | None = None,
    checked_at: datetime | None = None,
    today: date | None = None,
    archive_dir: str | Path | None = None,
) -> ResearchImportSummary:
    active_checked_at = checked_at or datetime.now(timezone.utc)
    active_today = today or active_checked_at.date()

    imported: list[ResearchImportItem] = []
    archived_paths: list[str] = []
    for path in paths:
        result = _load_result(path, kind=kind)
        for event in result.to_events():
            record = build_research_import_record(
                event,
                checked_at=active_checked_at,
                today=active_today,
                import_metadata={
                    "research_run_date": result.run_date.isoformat(),
                    "source_file": str(path),
                },
            )
            changed = store.record_validation(record)
            imported.append(
                ResearchImportItem(
                    path=str(path),
                    dedupe_key=event.dedupe_key,
                    event_type=event.event_type.value,
                    title=event.title,
                    changed=changed,
                )
            )
        archived_path = _archive_file(path, archive_dir=archive_dir)
        if archived_path is not None:
            archived_paths.append(archived_path)

    changed_count = sum(1 for item in imported if item.changed)
    return ResearchImportSummary(
        imported_file_count=len(paths),
        candidate_count=len(imported),
        changed_count=changed_count,
        archived_file_count=len(archived_paths),
        archived_paths=tuple(archived_paths),
        imported=tuple(imported),
    )


def build_research_import_record(
    event: NormalizedEvent,
    *,
    checked_at: datetime,
    today: date,
    import_metadata: Mapping[str, Any] | None = None,
) -> ValidationPersistenceRecord:
    artist_name = event.artist_names[0] if event.artist_names else event.title
    payload: dict[str, Any] = {
        "event": event.to_dict(),
        "checked_at": checked_at.isoformat(),
        "score": {
            "action": "ready_to_publish",
            "effective_status": "research_candidate",
            "artist_value_tier": None,
            "resolved_track_count": 0,
            "reasons": [f"imported from {event.source}"],
        },
        "research_import": {
            "source": event.source,
            "candidate_search_terms": list(event.candidate_search_terms),
            **dict(import_metadata or {}),
        },
    }
    return ValidationPersistenceRecord(
        dedupe_key=event.dedupe_key,
        source=event.source,
        source_event_id=event.source_event_id,
        event_type=event.event_type.value,
        artist_name=artist_name,
        project_title=event.title,
        event_date=event.event_date,
        spotify_artist_id=None,
        effective_status="research_candidate",
        artist_value_tier=None,
        prerelease_uri=None,
        resolved_track_uris=(),
        score_action="ready_to_publish",
        checked_at=checked_at,
        next_check_at=_compute_research_next_check_at(event, checked_at=checked_at, today=today),
        publish_pending=True,
        payload=payload,
    )


def infer_research_kind(path: str | Path) -> str:
    payload = _read_json_object(path)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("research file must include at least one candidate to infer kind")
    kinds: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("candidate payload must be an object")
        event_type = candidate.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("candidate is missing event_type")
        kinds.add(event_type.strip())
    if len(kinds) != 1:
        raise ValueError("research file must contain exactly one event_type across candidates")
    return next(iter(kinds))


def _load_result(path: str | Path, *, kind: str | None) -> ResearchResult:
    active_kind = (kind or infer_research_kind(path)).strip().lower()
    loader = _loader_for_kind(active_kind)
    return loader(path)


def _loader_for_kind(kind: str) -> ResearchLoader:
    if kind == "festival":
        return load_festival_research
    if kind == "tour":
        return load_tour_research
    if kind == "moment":
        return load_moment_research
    raise ValueError(f"unsupported research kind: {kind}")


def _read_json_object(path: str | Path) -> Mapping[str, Any]:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("research file must contain a JSON object")
    return payload


def _archive_file(path: str | Path, *, archive_dir: str | Path | None) -> str | None:
    if archive_dir is None:
        return None
    source_path = Path(path)
    target_dir = Path(archive_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    if target_path.exists():
        stem = source_path.stem
        suffix = source_path.suffix
        index = 1
        while True:
            candidate = target_dir / f"{stem}.{index}{suffix}"
            if not candidate.exists():
                target_path = candidate
                break
            index += 1
    move(str(source_path), str(target_path))
    return str(target_path)


def _compute_research_next_check_at(
    event: NormalizedEvent,
    *,
    checked_at: datetime,
    today: date,
) -> datetime:
    if event.event_date is None:
        return checked_at + timedelta(days=14)
    days_until = (event.event_date - today).days
    if days_until <= 0:
        return checked_at + timedelta(days=30)
    if days_until <= 7:
        return checked_at + timedelta(days=1)
    if days_until <= 30:
        return checked_at + timedelta(days=3)
    if days_until <= 90:
        return checked_at + timedelta(days=7)
    return checked_at + timedelta(days=14)
