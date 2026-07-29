"""Refresh timing helpers for candidate recheck scheduling."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def compute_next_check_at(
    *,
    event_date: date | None,
    today: date,
    effective_status: str,
) -> datetime:
    """Return when this candidate should be checked again."""

    now = datetime.now(timezone.utc)
    if event_date is None:
        return now + timedelta(hours=24)

    days_until = (event_date - today).days
    if effective_status == "album_confirmed":
        return now + timedelta(days=7)
    if days_until < 0:
        return now + timedelta(hours=24)
    if days_until == 0:
        return now + timedelta(hours=4)
    if days_until <= 14:
        return now + timedelta(hours=8)
    return now + timedelta(hours=24)


def track_uri_hash(uris: list[str]) -> str:
    return "|".join(sorted(uris))
