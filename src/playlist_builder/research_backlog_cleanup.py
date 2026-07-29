"""Clear unpublished research backlog so the daily pipeline can start clean."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from playlist_builder.persistence.env import project_root
from playlist_builder.persistence.models import CandidateStateRow

RESEARCH_SOURCES = frozenset({"festival_research", "tour_research", "moment_research"})
RESEARCH_EVENT_TYPES = frozenset({"festival", "tour", "moment"})
_PENDING_NAME_RE = re.compile(
    r"^(festival|tour|moment)_\d{4}-\d{2}-\d{2}(?:\.\d+)?\.json$"
)


class ResearchCleanupStore(Protocol):
    def list_candidates(
        self,
        *,
        limit: int = 50,
        publish_pending_only: bool = False,
    ) -> list[CandidateStateRow]: ...

    def delete_candidates(self, dedupe_keys: Sequence[str]) -> int: ...


@dataclass(frozen=True, slots=True)
class ResearchCleanupResult:
    days: int
    cutoff_iso: str
    matched_count: int
    deleted_count: int
    pending_cleared_count: int
    dry_run: bool
    matched: tuple[dict[str, Any], ...]
    pending_cleared: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "cutoff_iso": self.cutoff_iso,
            "matched_count": self.matched_count,
            "deleted_count": self.deleted_count,
            "pending_cleared_count": self.pending_cleared_count,
            "dry_run": self.dry_run,
            "matched": list(self.matched),
            "pending_cleared": list(self.pending_cleared),
        }


def pending_research_dir(*, root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "research" / "pending"


def is_research_candidate(row: CandidateStateRow) -> bool:
    return row.source in RESEARCH_SOURCES or row.event_type in RESEARCH_EVENT_TYPES


def candidate_activity_at(row: CandidateStateRow) -> datetime:
    if row.last_change_at is not None:
        return _as_utc(row.last_change_at)
    return _as_utc(row.last_checked_at)


def select_research_backlog(
    rows: Sequence[CandidateStateRow],
    *,
    cutoff: datetime,
    include_all_unpublished: bool = False,
) -> list[CandidateStateRow]:
    """Pick unpublished research rows to remove."""

    selected: list[CandidateStateRow] = []
    for row in rows:
        if not is_research_candidate(row):
            continue
        if row.spotify_playlist_id or row.spotify_playlist_uri or row.last_published_at:
            continue
        if include_all_unpublished or candidate_activity_at(row) >= cutoff:
            selected.append(row)
    selected.sort(key=lambda item: (item.event_date or "", item.dedupe_key))
    return selected


def list_pending_research_files(pending_dir: Path) -> list[Path]:
    if not pending_dir.is_dir():
        return []
    files = [
        path
        for path in sorted(pending_dir.glob("*.json"))
        if _PENDING_NAME_RE.match(path.name)
    ]
    return files


def clean_research_backlog(
    store: ResearchCleanupStore,
    *,
    days: int = 3,
    apply: bool = False,
    include_all_unpublished: bool = False,
    clear_pending_files: bool = True,
    pending_dir: Path | None = None,
    now: datetime | None = None,
    scan_limit: int = 5000,
) -> ResearchCleanupResult:
    if days < 1:
        raise ValueError("days must be >= 1")

    as_of = _as_utc(now or datetime.now(timezone.utc))
    cutoff = as_of - timedelta(days=days)
    rows = store.list_candidates(limit=scan_limit, publish_pending_only=False)
    matched = select_research_backlog(
        rows,
        cutoff=cutoff,
        include_all_unpublished=include_all_unpublished,
    )
    matched_summaries = tuple(_row_summary(row) for row in matched)

    pending_root = pending_dir if pending_dir is not None else pending_research_dir()
    pending_files = list_pending_research_files(pending_root) if clear_pending_files else []
    pending_paths = tuple(str(path) for path in pending_files)

    deleted_count = 0
    pending_cleared: list[str] = []
    if apply:
        if matched:
            deleted_count = store.delete_candidates([row.dedupe_key for row in matched])
        for path in pending_files:
            path.unlink(missing_ok=True)
            pending_cleared.append(str(path))

    return ResearchCleanupResult(
        days=days,
        cutoff_iso=cutoff.isoformat(),
        matched_count=len(matched),
        deleted_count=deleted_count,
        pending_cleared_count=len(pending_cleared) if apply else len(pending_files),
        dry_run=not apply,
        matched=matched_summaries,
        pending_cleared=tuple(pending_cleared if apply else pending_paths),
    )


def _row_summary(row: CandidateStateRow) -> dict[str, Any]:
    return {
        "dedupe_key": row.dedupe_key,
        "source": row.source,
        "event_type": row.event_type,
        "title": row.project_title,
        "event_date": row.event_date,
        "publish_pending": row.publish_pending,
        "last_checked_at": row.last_checked_at.isoformat(),
        "last_change_at": row.last_change_at.isoformat() if row.last_change_at else None,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
