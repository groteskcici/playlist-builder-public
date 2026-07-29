"""Build persistence records from validation pipeline output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from playlist_builder.events import NormalizedEvent
from playlist_builder.persistence.refresh import compute_next_check_at, track_uri_hash
from playlist_builder.persistence.resolved_tracks import extract_resolved_track_uris
from playlist_builder.spotify_matcher import SpotifyMatchResult
from playlist_builder.spotify_prerelease_enrichment import SpotifyPrereleaseEnrichmentResult


@dataclass(frozen=True, slots=True)
class ValidationPersistenceRecord:
    dedupe_key: str
    source: str
    source_event_id: str | None
    event_type: str
    artist_name: str
    project_title: str
    event_date: date | None
    spotify_artist_id: str | None
    effective_status: str
    artist_value_tier: str | None
    prerelease_uri: str | None
    resolved_track_uris: tuple[str, ...]
    score_action: str
    checked_at: datetime
    next_check_at: datetime
    publish_pending: bool
    payload: dict[str, Any]

    @property
    def resolved_track_count(self) -> int:
        return len(self.resolved_track_uris)

    @property
    def track_uri_hash(self) -> str:
        return track_uri_hash(list(self.resolved_track_uris))


def build_validation_record(
    *,
    event: NormalizedEvent,
    match: SpotifyMatchResult,
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None,
    checked_at: datetime,
    today: date,
    previous_uris: list[str] | None = None,
    previous_status: str | None = None,
    previous_score_action: str | None = None,
) -> ValidationPersistenceRecord:
    from playlist_builder.scoring import is_publishable_score, score_opportunity

    score = score_opportunity(
        event=event,
        match=match,
        enrichment=enrichment,
        today=today,
    )
    effective_status = score.effective_status
    resolved_uris = tuple(extract_resolved_track_uris(enrichment))
    artist_name = match.artist_name or (event.artist_names[0] if event.artist_names else "")
    project_title = match.project_title or event.title
    spotify_artist = match.spotify_artist or {}
    artist_value_tier = match.artist_value_tier.value if match.artist_value_tier else None
    prerelease_uri = _prerelease_uri(enrichment)
    changed = _state_changed(
        resolved_uris=list(resolved_uris),
        effective_status=effective_status,
        previous_uris=previous_uris,
        previous_status=previous_status,
        score_action=score.action.value,
        previous_score_action=previous_score_action,
    )
    publish_pending = is_publishable_score(score.action) and changed

    payload: dict[str, Any] = {
        "event": event.to_dict(),
        "match": match.to_dict(),
        "checked_at": checked_at.isoformat(),
        "score": score.to_dict(),
    }
    if enrichment is not None:
        payload["prerelease_enrichment"] = (
            enrichment.to_dict() if hasattr(enrichment, "to_dict") else dict(enrichment)
        )

    return ValidationPersistenceRecord(
        dedupe_key=event.dedupe_key,
        source=event.source,
        source_event_id=event.source_event_id,
        event_type=event.event_type.value,
        artist_name=artist_name,
        project_title=project_title,
        event_date=event.event_date,
        spotify_artist_id=_optional_str(spotify_artist.get("id")),
        effective_status=effective_status,
        artist_value_tier=artist_value_tier,
        prerelease_uri=prerelease_uri,
        resolved_track_uris=resolved_uris,
        score_action=score.action.value,
        checked_at=checked_at,
        next_check_at=compute_next_check_at(
            event_date=event.event_date,
            today=today,
            effective_status=effective_status,
        ),
        publish_pending=publish_pending,
        payload=payload,
    )


def _prerelease_uri(
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None,
) -> str | None:
    if enrichment is None:
        return None
    if hasattr(enrichment, "prerelease_uri"):
        return enrichment.prerelease_uri
    value = enrichment.get("prerelease_uri")
    return value if isinstance(value, str) else None


def _state_changed(
    *,
    resolved_uris: list[str],
    effective_status: str,
    previous_uris: list[str] | None,
    previous_status: str | None,
    score_action: str,
    previous_score_action: str | None,
) -> bool:
    if previous_uris is None and previous_status is None and previous_score_action is None:
        return True
    if previous_status != effective_status:
        return True
    if previous_score_action != score_action:
        return True
    return track_uri_hash(resolved_uris) != track_uri_hash(previous_uris or [])


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def payload_to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
