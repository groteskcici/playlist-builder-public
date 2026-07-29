"""Enrich Spotify album matches with prerelease countdown track URI resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Protocol

from playlist_builder.events import NormalizedEvent
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
)
from playlist_builder.spotify_prerelease_resolver import (
    SpotifyPrereleaseResolution,
    SpotifyPrereleaseResolverConfig,
    SpotifyPrereleaseTrackResolver,
)
from playlist_builder.spotify_ui_scraper import (
    SpotifyArtistUiScraper,
    SpotifyArtistUiSnapshot,
    SpotifyPrereleaseSnapshot,
    SpotifyReleaseCountdown,
)


class SpotifyPrereleaseUiClient(Protocol):
    def scrape_artist(self, artist: str) -> SpotifyArtistUiSnapshot:
        """Return public Spotify artist UI snapshot."""

    def scrape_prerelease(self, prerelease: str) -> SpotifyPrereleaseSnapshot:
        """Return public Spotify prerelease page snapshot."""


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseEnrichmentConfig:
    min_countdown_title_similarity: float = 0.72
    pre_release_window_days: int | None = None
    high_tier_min_resolved_tracks: int = 1
    review_tier_min_resolved_tracks: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.min_countdown_title_similarity <= 1:
            raise ValueError("min_countdown_title_similarity must be between 0 and 1")
        if self.pre_release_window_days is not None and self.pre_release_window_days < 0:
            raise ValueError("pre_release_window_days must not be negative")
        if self.high_tier_min_resolved_tracks <= 0:
            raise ValueError("high_tier_min_resolved_tracks must be positive")
        if self.review_tier_min_resolved_tracks <= 0:
            raise ValueError("review_tier_min_resolved_tracks must be positive")


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseEnrichmentResult:
    base_match: SpotifyMatchResult
    prerelease_uri: str | None = None
    prerelease_title: str | None = None
    countdown_title_similarity: float = 0.0
    resolution: SpotifyPrereleaseResolution | None = None
    qualified: bool = False
    reason: str | None = None

    @property
    def is_pre_release_opportunity(self) -> bool:
        return self.effective_status == SpotifyMatchStatus.PRE_RELEASE_CANDIDATE

    @property
    def effective_status(self) -> SpotifyMatchStatus:
        if self.qualified:
            return SpotifyMatchStatus.PRE_RELEASE_CANDIDATE
        return self.base_match.status

    def to_dict(self) -> dict[str, object]:
        return {
            "effective_status": self.effective_status.value,
            "is_pre_release_opportunity": self.is_pre_release_opportunity,
            "base_match": self.base_match.to_dict(),
            "prerelease_uri": self.prerelease_uri,
            "prerelease_title": self.prerelease_title,
            "countdown_title_similarity": round(self.countdown_title_similarity, 3),
            "resolved_track_count": (
                len(self.resolution.matched_tracks) if self.resolution else 0
            ),
            "unresolved_track_count": (
                len(self.resolution.unmatched_tracks) if self.resolution else 0
            ),
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "qualified": self.qualified,
            "reason": self.reason,
        }


class SpotifyPrereleaseEnricher:
    """Find matching release countdowns and resolve usable prerelease track URIs."""

    def __init__(
        self,
        ui_client: SpotifyPrereleaseUiClient,
        resolver: SpotifyPrereleaseTrackResolver,
        config: SpotifyPrereleaseEnrichmentConfig | None = None,
    ) -> None:
        self._ui_client = ui_client
        self._resolver = resolver
        self._config = config or SpotifyPrereleaseEnrichmentConfig()

    @classmethod
    def from_env(
        cls,
        *,
        market: str | None = None,
        headless: bool = True,
        timeout_ms: int = 45_000,
        config: SpotifyPrereleaseEnrichmentConfig | None = None,
    ) -> "SpotifyPrereleaseEnricher":
        return cls(
            SpotifyArtistUiScraper(headless=headless, timeout_ms=timeout_ms),
            SpotifyPrereleaseTrackResolver.from_env(
                SpotifyPrereleaseResolverConfig(market=market)
            ),
            config=config,
        )

    def enrich(
        self,
        event: NormalizedEvent,
        base_match: SpotifyMatchResult,
        *,
        today: date | None = None,
    ) -> SpotifyPrereleaseEnrichmentResult:
        active_today = today or datetime.now(timezone.utc).date()
        if base_match.spotify_artist is None:
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                reason="no Spotify artist available for prerelease lookup",
            )
        if base_match.artist_value_tier is None or base_match.artist_value_tier == ArtistValueTier.LOW:
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                reason="artist tier is not eligible for prerelease enrichment",
            )
        if not _within_prerelease_window(
            event.event_date,
            active_today,
            self._config.pre_release_window_days,
        ):
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                reason="event date is outside prerelease enrichment window",
            )

        project_title = _project_title_from_event(event, base_match)
        artist_id = str(base_match.spotify_artist.get("id", ""))
        if not artist_id:
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                reason="Spotify artist payload did not include an ID",
            )

        artist_snapshot = self._ui_client.scrape_artist(artist_id)
        countdown, countdown_similarity = _best_countdown(
            artist_snapshot.release_countdowns,
            project_title,
            min_similarity=self._config.min_countdown_title_similarity,
        )
        if countdown is None:
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                reason="no matching Spotify release countdown found",
            )

        prerelease_snapshot = self._ui_client.scrape_prerelease(countdown.uri)
        resolution = self._resolver.resolve(prerelease_snapshot)
        min_tracks = (
            self._config.high_tier_min_resolved_tracks
            if base_match.artist_value_tier == ArtistValueTier.HIGH
            else self._config.review_tier_min_resolved_tracks
        )
        matched_count = len(resolution.matched_tracks)
        if matched_count < min_tracks:
            return SpotifyPrereleaseEnrichmentResult(
                base_match=base_match,
                prerelease_uri=countdown.uri,
                prerelease_title=countdown.title,
                countdown_title_similarity=countdown_similarity,
                resolution=resolution,
                reason=(
                    f"only {matched_count} prerelease track URI(s) resolved; "
                    f"needed {min_tracks}"
                ),
            )

        return SpotifyPrereleaseEnrichmentResult(
            base_match=base_match,
            prerelease_uri=countdown.uri,
            prerelease_title=countdown.title,
            countdown_title_similarity=countdown_similarity,
            resolution=resolution,
            qualified=True,
            reason="resolved enough official Spotify prerelease track URIs",
        )


def _within_prerelease_window(
    event_date: date | None,
    today: date,
    window_days: int | None,
) -> bool:
    if event_date is None:
        return False
    delta = (event_date - today).days
    if delta < 0:
        return False
    if window_days is None:
        return True
    return delta <= window_days


def _project_title_from_event(
    event: NormalizedEvent,
    base_match: SpotifyMatchResult,
) -> str:
    raw_title = event.raw_payload.get("project_title")
    if isinstance(raw_title, str) and raw_title.strip():
        return raw_title.strip()
    return base_match.project_title


def _best_countdown(
    countdowns: tuple[SpotifyReleaseCountdown, ...],
    project_title: str,
    *,
    min_similarity: float,
) -> tuple[SpotifyReleaseCountdown | None, float]:
    scored = [
        (countdown, _similarity(project_title, countdown.title or ""))
        for countdown in countdowns
        if countdown.title
    ]
    if not scored:
        return None, 0.0

    countdown, score = max(scored, key=lambda item: item[1])
    if score < min_similarity:
        return None, score
    return countdown, score


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )
