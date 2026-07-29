"""Shared persistence models and row parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


class PersistenceError(RuntimeError):
    """Raised when persistence operations fail."""


@dataclass(frozen=True, slots=True)
class CandidateStateRow:
    dedupe_key: str
    source: str
    source_event_id: str | None
    event_type: str
    artist_name: str
    project_title: str
    event_date: str | None
    spotify_artist_id: str | None
    effective_status: str
    artist_value_tier: str | None
    prerelease_uri: str | None
    resolved_track_count: int
    resolved_track_uris: list[str]
    track_uri_hash: str
    score_action: str
    last_checked_at: datetime
    last_change_at: datetime | None
    next_check_at: datetime
    publish_pending: bool
    spotify_playlist_id: str | None
    spotify_playlist_uri: str | None
    last_published_at: datetime | None
    spotify_publish_slot: str | None = None


def parse_candidate_state_row(row: tuple[Any, ...]) -> CandidateStateRow:
    return CandidateStateRow(
        dedupe_key=row[0],
        source=row[1],
        source_event_id=row[2],
        event_type=row[3],
        artist_name=row[4],
        project_title=row[5],
        event_date=_parse_date_value(row[6]),
        spotify_artist_id=row[7],
        effective_status=row[8],
        artist_value_tier=row[9],
        prerelease_uri=row[10],
        resolved_track_count=int(row[11]),
        resolved_track_uris=_parse_json_list(row[12]),
        track_uri_hash=row[13],
        score_action=str(row[14]),
        last_checked_at=_parse_datetime_value(row[15]),
        last_change_at=_parse_datetime_value(row[16]) if row[16] is not None else None,
        next_check_at=_parse_datetime_value(row[17]),
        publish_pending=bool(row[18]),
        spotify_playlist_id=_optional_str(row[19]),
        spotify_playlist_uri=_optional_str(row[20]),
        last_published_at=(
            _parse_datetime_value(row[21]) if row[21] is not None else None
        ),
        spotify_publish_slot=_optional_str(row[22]) if len(row) > 22 else None,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"unsupported datetime value: {value!r}")


def _parse_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    raise TypeError(f"unsupported JSON list value: {value!r}")
