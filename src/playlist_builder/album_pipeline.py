"""Shared album validation, scoring, and persistence pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Protocol

from playlist_builder.events import NormalizedEvent
from playlist_builder.persistence.validation_record import (
    ValidationPersistenceRecord,
    build_validation_record,
)
from playlist_builder.scoring import ScoreResult, score_opportunity
from playlist_builder.spotify_matcher import SpotifyMatcher, SpotifyMatchResult
from playlist_builder.spotify_prerelease_enrichment import (
    SpotifyPrereleaseEnricher,
    SpotifyPrereleaseEnrichmentResult,
)


class ValidationStore(Protocol):
    def get_candidate_state(self, dedupe_key: str): ...

    def record_validation(self, record: ValidationPersistenceRecord) -> bool: ...


@dataclass(frozen=True, slots=True)
class AlbumValidationOutcome:
    event: NormalizedEvent
    match: SpotifyMatchResult
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None
    score: ScoreResult
    record: ValidationPersistenceRecord | None = None
    changed: bool = False
    publish_pending: bool = False
    enrichment_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": self.event.to_dict(),
            "match": self.match.to_dict(),
            "score": self.score.to_dict(),
        }
        if self.enrichment is not None:
            payload["prerelease_enrichment"] = (
                self.enrichment.to_dict()
                if hasattr(self.enrichment, "to_dict")
                else dict(self.enrichment)
            )
        if self.record is not None:
            payload["persistence"] = {
                "dedupe_key": self.record.dedupe_key,
                "effective_status": self.record.effective_status,
                "score_action": self.record.score_action,
                "next_check_at": self.record.next_check_at.isoformat(),
                "publish_pending": self.record.publish_pending,
                "changed": self.changed,
            }
        if self.enrichment_error:
            payload["enrichment_error"] = self.enrichment_error
        return payload


def validate_album_event(
    event: NormalizedEvent,
    *,
    matcher: SpotifyMatcher,
    enricher: SpotifyPrereleaseEnricher | None,
    today: date,
    checked_at: datetime | None = None,
) -> AlbumValidationOutcome:
    match = matcher.validate_album_candidate(event, today=today)
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None = None
    enrichment_error = None

    if enricher is not None:
        try:
            enrichment = enricher.enrich(event, match, today=today)
        except Exception as exc:
            enrichment_error = f"{type(exc).__name__}: {exc}"
            enrichment = {
                "reason": enrichment_error,
                "qualified": False,
            }

    score = score_opportunity(
        event=event,
        match=match,
        enrichment=enrichment,
        today=today,
    )

    return AlbumValidationOutcome(
        event=event,
        match=match,
        enrichment=enrichment,
        score=score,
        enrichment_error=enrichment_error,
    )


def persist_album_validation(
    outcome: AlbumValidationOutcome,
    store: ValidationStore,
    *,
    today: date,
    checked_at: datetime,
) -> AlbumValidationOutcome:
    previous = store.get_candidate_state(outcome.event.dedupe_key)
    record = build_validation_record(
        event=outcome.event,
        match=outcome.match,
        enrichment=outcome.enrichment,
        checked_at=checked_at,
        today=today,
        previous_uris=previous.resolved_track_uris if previous else None,
        previous_status=previous.effective_status if previous else None,
        previous_score_action=previous.score_action if previous else None,
    )
    changed = store.record_validation(record)
    return AlbumValidationOutcome(
        event=outcome.event,
        match=outcome.match,
        enrichment=outcome.enrichment,
        score=outcome.score,
        record=record,
        changed=changed,
        publish_pending=record.publish_pending,
        enrichment_error=outcome.enrichment_error,
    )


def event_from_payload(payload: Mapping[str, Any]) -> NormalizedEvent:
    event_data = payload.get("event")
    if not isinstance(event_data, Mapping):
        raise ValueError("stored payload is missing event data")
    return NormalizedEvent.from_dict(event_data)
