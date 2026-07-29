"""Spotify artist tier cache to avoid repeat API calls for low-value discoveries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from playlist_builder.events import NormalizedEvent
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
    SpotifyMatcherConfig,
    _artist_value_tier,
)


class TierCacheDisposition(str, Enum):
    HARD_SKIP = "hard_skip"
    WATCH = "watch"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class TierCacheConfig:
    hard_skip_max_popularity: int = 44
    watch_min_popularity: int = 45
    hard_skip_max_followers: int = 400_000
    not_found_recheck_days: int = 14
    hard_skip_ttl_days: int | None = 180

    def __post_init__(self) -> None:
        if self.hard_skip_max_popularity < 0:
            raise ValueError("hard_skip_max_popularity must not be negative")
        if self.watch_min_popularity < 0:
            raise ValueError("watch_min_popularity must not be negative")
        if self.hard_skip_max_followers < 0:
            raise ValueError("hard_skip_max_followers must not be negative")
        if self.not_found_recheck_days <= 0:
            raise ValueError("not_found_recheck_days must be positive")


@dataclass(frozen=True, slots=True)
class AlbumTierCacheRow:
    cache_key: str
    spotify_artist_id: str | None
    genius_artist_name: str
    last_popularity: int | None
    last_follower_count: int | None
    disposition: str
    reason: str
    checked_at: datetime
    next_check_at: datetime | None
    release_date: date | None = None


@runtime_checkable
class TierCacheStore(Protocol):
    def get_tier_cache(self, cache_key: str) -> AlbumTierCacheRow | None: ...

    def get_tier_cache_by_genius_name(self, genius_artist_name: str) -> AlbumTierCacheRow | None: ...

    def upsert_tier_cache(
        self,
        *,
        cache_key: str,
        spotify_artist_id: str | None,
        genius_artist_name: str,
        last_popularity: int | None,
        last_follower_count: int | None,
        disposition: str,
        reason: str,
        checked_at: datetime,
        next_check_at: datetime | None,
        release_date: date | None,
    ) -> None: ...

    def delete_tier_cache(self, cache_key: str) -> None: ...


def normalize_artist_name(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )


def genius_cache_key(artist_name: str) -> str:
    normalized = normalize_artist_name(artist_name)
    if not normalized:
        raise ValueError("artist_name must not be blank")
    return f"genius:{normalized}"


def spotify_artist_cache_key(artist_id: str) -> str:
    artist_id = artist_id.strip()
    if not artist_id:
        raise ValueError("artist_id must not be blank")
    return f"spotify:artist:{artist_id}"


def follower_count(artist: Mapping[str, Any] | None) -> int | None:
    if artist is None:
        return None
    followers = artist.get("followers")
    if isinstance(followers, dict):
        total = followers.get("total")
        if isinstance(total, int):
            return total
    return None


def popularity(artist: Mapping[str, Any] | None) -> int | None:
    if artist is None:
        return None
    value = artist.get("popularity")
    return value if isinstance(value, int) else None


def classify_tier_cache_disposition(
    *,
    match: SpotifyMatchResult,
    matcher_config: SpotifyMatcherConfig,
    cache_config: TierCacheConfig | None = None,
) -> tuple[TierCacheDisposition, str] | None:
    """Return cache disposition for non-pipeline artists, or None if tier-qualified."""
    rules = cache_config or TierCacheConfig()

    if match.status == SpotifyMatchStatus.ARTIST_NOT_FOUND:
        return (
            TierCacheDisposition.NOT_FOUND,
            match.reason or "no confident Spotify artist match",
        )

    artist = match.spotify_artist
    tier = match.artist_value_tier
    if tier is None and artist is not None:
        tier = _artist_value_tier(artist, matcher_config)

    if tier in {ArtistValueTier.HIGH, ArtistValueTier.REVIEW}:
        return None

    pop = popularity(artist)
    followers = follower_count(artist)

    if pop is not None and pop <= rules.hard_skip_max_popularity:
        if followers is None or followers < matcher_config.legacy_review_followers:
            return (
                TierCacheDisposition.HARD_SKIP,
                f"popularity {pop} below watch threshold ({rules.hard_skip_max_popularity})",
            )

    if (
        pop is not None
        and pop < matcher_config.review_value_popularity
        and (followers is None or followers < rules.hard_skip_max_followers)
        and (followers is None or followers < matcher_config.legacy_review_followers)
    ):
        return (
            TierCacheDisposition.HARD_SKIP,
            f"popularity {pop} and followers {followers} below review thresholds",
        )

    if match.status == SpotifyMatchStatus.ARTIST_TOO_SMALL or tier == ArtistValueTier.LOW:
        return (
            TierCacheDisposition.WATCH,
            match.reason or "artist below value tier but may improve closer to release",
        )

    return (
        TierCacheDisposition.WATCH,
        match.reason or "artist not yet qualified for playlist pipeline",
    )


def compute_tier_cache_next_check_at(
    disposition: TierCacheDisposition,
    *,
    event_date: date | None,
    today: date,
    checked_at: datetime,
    cache_config: TierCacheConfig | None = None,
) -> datetime | None:
    rules = cache_config or TierCacheConfig()

    if disposition == TierCacheDisposition.HARD_SKIP:
        if rules.hard_skip_ttl_days is None:
            return None
        return checked_at + timedelta(days=rules.hard_skip_ttl_days)

    if disposition == TierCacheDisposition.NOT_FOUND:
        return checked_at + timedelta(days=rules.not_found_recheck_days)

    days_until = (event_date - today).days if event_date is not None else None
    if days_until is None:
        return checked_at + timedelta(days=14)
    if days_until > 60:
        return checked_at + timedelta(days=14)
    if days_until > 30:
        return checked_at + timedelta(days=7)
    if days_until > 14:
        return checked_at + timedelta(days=3)
    return checked_at + timedelta(days=1)


def tier_cache_should_skip(
    row: AlbumTierCacheRow | None,
    *,
    as_of: datetime,
) -> bool:
    if row is None:
        return False
    if row.next_check_at is None:
        return row.disposition == TierCacheDisposition.HARD_SKIP.value
    return row.next_check_at > as_of


class AlbumTierCacheGate:
    def __init__(
        self,
        *,
        matcher_config: SpotifyMatcherConfig,
        cache_config: TierCacheConfig | None = None,
    ) -> None:
        self._matcher_config = matcher_config
        self._cache_config = cache_config or TierCacheConfig()

    @classmethod
    def from_matcher_config(
        cls,
        matcher_config: SpotifyMatcherConfig,
        *,
        cache_config: TierCacheConfig | None = None,
    ) -> "AlbumTierCacheGate":
        return cls(matcher_config=matcher_config, cache_config=cache_config)

    def lookup_cached_skip(
        self,
        store: TierCacheStore,
        event: NormalizedEvent,
        *,
        as_of: datetime,
    ) -> AlbumTierCacheRow | None:
        artist_name = event.artist_names[0] if event.artist_names else ""
        if not artist_name:
            return None

        row = store.get_tier_cache_by_genius_name(artist_name)
        if tier_cache_should_skip(row, as_of=as_of):
            return row

        spotify_artist_id = _spotify_artist_id_from_event(event)
        if spotify_artist_id:
            spotify_row = store.get_tier_cache(spotify_artist_cache_key(spotify_artist_id))
            if tier_cache_should_skip(spotify_row, as_of=as_of):
                return spotify_row
        return None

    def record_match(
        self,
        store: TierCacheStore,
        event: NormalizedEvent,
        match: SpotifyMatchResult,
        *,
        today: date,
        checked_at: datetime | None = None,
    ) -> None:
        active_checked_at = checked_at or datetime.now(timezone.utc)
        artist_name = match.artist_name or (event.artist_names[0] if event.artist_names else "")
        if not artist_name:
            return

        classification = classify_tier_cache_disposition(
            match=match,
            matcher_config=self._matcher_config,
            cache_config=self._cache_config,
        )
        spotify_artist_id = None
        if match.spotify_artist and isinstance(match.spotify_artist.get("id"), str):
            spotify_artist_id = match.spotify_artist["id"]

        if classification is None:
            store.delete_tier_cache(genius_cache_key(artist_name))
            if spotify_artist_id:
                store.delete_tier_cache(spotify_artist_cache_key(spotify_artist_id))
            return

        disposition, reason = classification
        next_check_at = compute_tier_cache_next_check_at(
            disposition,
            event_date=event.event_date,
            today=today,
            checked_at=active_checked_at,
            cache_config=self._cache_config,
        )
        payload = dict(
            spotify_artist_id=spotify_artist_id,
            genius_artist_name=artist_name,
            last_popularity=popularity(match.spotify_artist),
            last_follower_count=follower_count(match.spotify_artist),
            disposition=disposition.value,
            reason=reason,
            checked_at=active_checked_at,
            next_check_at=next_check_at,
            release_date=event.event_date,
        )
        store.upsert_tier_cache(cache_key=genius_cache_key(artist_name), **payload)
        if spotify_artist_id:
            store.upsert_tier_cache(
                cache_key=spotify_artist_cache_key(spotify_artist_id),
                **payload,
            )


def _spotify_artist_id_from_event(event: NormalizedEvent) -> str | None:
    raw = event.raw_payload.get("spotify_artist_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
