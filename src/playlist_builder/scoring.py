"""Rule-based opportunity scoring for album and pre-release candidates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

from playlist_builder.events import NormalizedEvent
from playlist_builder.persistence.resolved_tracks import extract_resolved_track_uris
from playlist_builder.spotify_album_watcher import _parse_release_date
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
)
from playlist_builder.spotify_prerelease_enrichment import SpotifyPrereleaseEnrichmentResult


class ScoreAction(str, Enum):
    IGNORE = "ignore"
    QUEUE_FOR_REVIEW = "queue_for_review"
    READY_TO_PUBLISH = "ready_to_publish"


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    max_album_age_days: int = 7
    auto_publish_high_tier_album_drops: bool = True

    def __post_init__(self) -> None:
        if self.max_album_age_days < 0:
            raise ValueError("max_album_age_days must not be negative")


@dataclass(frozen=True, slots=True)
class ScoreResult:
    action: ScoreAction
    effective_status: str
    artist_value_tier: str | None
    resolved_track_count: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "effective_status": self.effective_status,
            "artist_value_tier": self.artist_value_tier,
            "resolved_track_count": self.resolved_track_count,
            "reasons": list(self.reasons),
        }


def score_opportunity(
    *,
    event: NormalizedEvent,
    match: SpotifyMatchResult,
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None = None,
    today: date,
    config: ScoringConfig | None = None,
) -> ScoreResult:
    active_config = config or ScoringConfig()
    effective_status = _effective_status(match, enrichment)
    tier = match.artist_value_tier
    tier_value = tier.value if tier is not None else None
    resolved_uris = extract_resolved_track_uris(enrichment)
    reasons: list[str] = []

    if tier is None or tier == ArtistValueTier.LOW:
        reasons.append("artist below value tier thresholds")
        return _result(
            ScoreAction.IGNORE,
            effective_status,
            tier_value,
            resolved_uris,
            reasons,
        )

    if effective_status in {
        SpotifyMatchStatus.ARTIST_NOT_FOUND.value,
        SpotifyMatchStatus.ARTIST_TOO_SMALL.value,
        SpotifyMatchStatus.AMBIGUOUS_MATCH.value,
    }:
        reasons.append(f"match status is {effective_status}")
        return _result(
            ScoreAction.IGNORE,
            effective_status,
            tier_value,
            resolved_uris,
            reasons,
        )

    if effective_status == SpotifyMatchStatus.ALBUM_NOT_RELEASED.value:
        reasons.append("album not released and no qualified pre-release opportunity")
        return _result(
            ScoreAction.IGNORE,
            effective_status,
            tier_value,
            resolved_uris,
            reasons,
        )

    if effective_status == SpotifyMatchStatus.PRE_RELEASE_CANDIDATE.value:
        if not _is_qualified(enrichment):
            reasons.append("pre-release candidate is not qualified")
            return _result(
                ScoreAction.IGNORE,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )
        if not resolved_uris:
            reasons.append("pre-release candidate has no resolved track URIs")
            return _result(
                ScoreAction.IGNORE,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )
        reasons.append("pre-release opportunities require human review")
        return _result(
            ScoreAction.QUEUE_FOR_REVIEW,
            effective_status,
            tier_value,
            resolved_uris,
            reasons,
        )

    if effective_status == SpotifyMatchStatus.ALBUM_CONFIRMED.value:
        if os.environ.get("ALBUM_INCLUDE_CONFIRMED_DROPS", "0").strip().lower() not in {"1", "true", "yes", "on"}:
            reasons.append("confirmed album drops disabled for current run")
            return _result(
                ScoreAction.IGNORE,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )

        album = match.spotify_album
        if album is None:
            reasons.append("album confirmed status without Spotify album payload")
            return _result(
                ScoreAction.IGNORE,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )

        release_date = _parse_release_date(str(album.get("release_date", "")))
        age_days = (today - release_date).days
        if age_days > active_config.max_album_age_days:
            reasons.append(
                f"album release is {age_days} days old; "
                f"max is {active_config.max_album_age_days}"
            )
            return _result(
                ScoreAction.IGNORE,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )

        if tier == ArtistValueTier.HIGH and active_config.auto_publish_high_tier_album_drops:
            reasons.append("high-tier confirmed album within freshness window")
            return _result(
                ScoreAction.READY_TO_PUBLISH,
                effective_status,
                tier_value,
                resolved_uris,
                reasons,
            )

        reasons.append("review-tier confirmed album within freshness window")
        return _result(
            ScoreAction.QUEUE_FOR_REVIEW,
            effective_status,
            tier_value,
            resolved_uris,
            reasons,
        )

    reasons.append(f"unsupported effective status: {effective_status}")
    return _result(
        ScoreAction.IGNORE,
        effective_status,
        tier_value,
        resolved_uris,
        reasons,
    )


def is_publishable_score(action: ScoreAction) -> bool:
    return action in {
        ScoreAction.READY_TO_PUBLISH,
        ScoreAction.QUEUE_FOR_REVIEW,
    }


def _result(
    action: ScoreAction,
    effective_status: str,
    artist_value_tier: str | None,
    resolved_uris: list[str],
    reasons: list[str],
) -> ScoreResult:
    return ScoreResult(
        action=action,
        effective_status=effective_status,
        artist_value_tier=artist_value_tier,
        resolved_track_count=len(resolved_uris),
        reasons=tuple(reasons),
    )


def _effective_status(
    match: SpotifyMatchResult,
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None,
) -> str:
    if enrichment is not None:
        if hasattr(enrichment, "effective_status"):
            return enrichment.effective_status.value
        status = enrichment.get("effective_status")
        if isinstance(status, str):
            return status
    return match.status.value


def _is_qualified(
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None,
) -> bool:
    if enrichment is None:
        return False
    if hasattr(enrichment, "qualified"):
        return bool(enrichment.qualified)
    return bool(enrichment.get("qualified"))
