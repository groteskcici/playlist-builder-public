"""Spotify matching and validation for album opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Mapping, Protocol

from playlist_builder.events import NormalizedEvent
from playlist_builder.spotify_album_watcher import (
    SpotifyWebAPIClient,
    _classify_album_kind,
    _parse_release_date,
)


class SpotifyMatcherClient(Protocol):
    """Spotify operations needed by the matcher."""

    def search_artists(
        self,
        query: str,
        *,
        limit: int = 5,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """Search Spotify artists."""

    def search_albums(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """Search Spotify albums, EPs, and singles."""

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """Search Spotify tracks."""

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        market: str | None = None,
        limit: int = 30,
    ) -> list[Mapping[str, Any]]:
        """List an artist's albums/singles."""

    def get_album_tracks(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """List full track payloads for an album or single."""


class SpotifyMatchStatus(str, Enum):
    ARTIST_NOT_FOUND = "artist_not_found"
    ARTIST_TOO_SMALL = "artist_too_small"
    ALBUM_NOT_RELEASED = "album_not_released"
    ALBUM_CONFIRMED = "album_confirmed"
    PRE_RELEASE_CANDIDATE = "pre_release_candidate"
    AMBIGUOUS_MATCH = "ambiguous_match"


class ArtistValueTier(str, Enum):
    HIGH = "high"
    REVIEW = "review"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class SpotifyMatcherConfig:
    market: str | None = None
    high_value_popularity: int = 80
    review_value_popularity: int = 65
    review_value_followers: int = 1_000_000
    legacy_review_followers: int = 5_000_000
    min_artist_similarity: float = 0.82
    min_title_similarity: float = 0.72
    ep_min_track_count: int = 4
    pre_release_window_days: int | None = None
    pre_release_min_tracks: int = 2
    high_tier_pre_release_min_tracks: int = 1
    recent_single_lookback_days: int = 180
    search_limit: int = 10

    def __post_init__(self) -> None:
        if not 0 <= self.high_value_popularity <= 100:
            raise ValueError("high_value_popularity must be between 0 and 100")
        if not 0 <= self.review_value_popularity <= 100:
            raise ValueError("review_value_popularity must be between 0 and 100")
        if self.review_value_popularity > self.high_value_popularity:
            raise ValueError("review_value_popularity must not exceed high_value_popularity")
        if self.review_value_followers < 0:
            raise ValueError("review_value_followers must not be negative")
        if self.legacy_review_followers < 0:
            raise ValueError("legacy_review_followers must not be negative")
        if not 0 <= self.min_artist_similarity <= 1:
            raise ValueError("min_artist_similarity must be between 0 and 1")
        if not 0 <= self.min_title_similarity <= 1:
            raise ValueError("min_title_similarity must be between 0 and 1")
        if self.ep_min_track_count <= 1:
            raise ValueError("ep_min_track_count must be greater than 1")
        if self.pre_release_window_days is not None and self.pre_release_window_days < 0:
            raise ValueError("pre_release_window_days must not be negative")
        if self.pre_release_min_tracks <= 0:
            raise ValueError("pre_release_min_tracks must be positive")
        if self.high_tier_pre_release_min_tracks <= 0:
            raise ValueError("high_tier_pre_release_min_tracks must be positive")
        if self.recent_single_lookback_days < 0:
            raise ValueError("recent_single_lookback_days must not be negative")
        if self.search_limit <= 0:
            raise ValueError("search_limit must be positive")


@dataclass(frozen=True, slots=True)
class SpotifyMatchResult:
    status: SpotifyMatchStatus
    artist_name: str
    project_title: str
    spotify_artist: Mapping[str, Any] | None = None
    spotify_album: Mapping[str, Any] | None = None
    artist_similarity: float = 0.0
    title_similarity: float = 0.0
    album_kind: str | None = None
    artist_value_tier: ArtistValueTier | None = None
    available_tracks: tuple[Mapping[str, Any], ...] = ()
    reason: str | None = None

    @property
    def is_actionable(self) -> bool:
        return self.status in {
            SpotifyMatchStatus.ALBUM_CONFIRMED,
            SpotifyMatchStatus.PRE_RELEASE_CANDIDATE,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "artist_name": self.artist_name,
            "project_title": self.project_title,
            "spotify_artist": _artist_payload(self.spotify_artist),
            "spotify_album": _album_payload(self.spotify_album),
            "artist_similarity": round(self.artist_similarity, 3),
            "title_similarity": round(self.title_similarity, 3),
            "album_kind": self.album_kind,
            "artist_value_tier": (
                self.artist_value_tier.value if self.artist_value_tier else None
            ),
            "available_tracks": [
                _track_payload(track) for track in self.available_tracks
            ],
            "reason": self.reason,
            "is_actionable": self.is_actionable,
        }


class SpotifyMatcher:
    """Match normalized album candidates against Spotify."""

    def __init__(
        self,
        client: SpotifyMatcherClient,
        config: SpotifyMatcherConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or SpotifyMatcherConfig()

    @classmethod
    def from_env(
        cls,
        config: SpotifyMatcherConfig | None = None,
    ) -> "SpotifyMatcher":
        return cls(SpotifyWebAPIClient.from_env(), config=config)

    def validate_album_candidate(
        self,
        event: NormalizedEvent,
        *,
        today: date | None = None,
    ) -> SpotifyMatchResult:
        active_today = today or datetime.now(timezone.utc).date()
        artist_name = event.artist_names[0] if event.artist_names else ""
        project_title = _project_title_from_event(event)

        if not artist_name or not project_title:
            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ARTIST_NOT_FOUND,
                artist_name=artist_name,
                project_title=project_title,
                reason="event did not include an artist and project title",
            )

        artist, artist_similarity = self._best_artist_match(artist_name)
        if artist is None:
            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ARTIST_NOT_FOUND,
                artist_name=artist_name,
                project_title=project_title,
                reason="no confident Spotify artist match",
            )

        artist_value_tier = _artist_value_tier(artist, self._config)
        if artist_value_tier == ArtistValueTier.LOW:
            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ARTIST_TOO_SMALL,
                artist_name=artist_name,
                project_title=project_title,
                spotify_artist=artist,
                artist_similarity=artist_similarity,
                artist_value_tier=artist_value_tier,
                reason="artist below popularity/follower value thresholds",
            )

        album, title_similarity = self._best_album_match(
            artist_name=artist_name,
            project_title=project_title,
            spotify_artist=artist,
        )
        if album is None:
            available_tracks = self._available_pre_release_tracks(
                event=event,
                artist_name=artist_name,
                project_title=project_title,
                spotify_artist=artist,
                today=active_today,
            )
            min_tracks = (
                self._config.high_tier_pre_release_min_tracks
                if artist_value_tier == ArtistValueTier.HIGH
                else self._config.pre_release_min_tracks
            )
            release_window_ok = _is_upcoming_release(
                event.event_date,
                active_today,
                self._config.pre_release_window_days,
            )
            if release_window_ok and len(available_tracks) >= min_tracks:
                return SpotifyMatchResult(
                    status=SpotifyMatchStatus.PRE_RELEASE_CANDIDATE,
                    artist_name=artist_name,
                    project_title=project_title,
                    spotify_artist=artist,
                    artist_similarity=artist_similarity,
                    artist_value_tier=artist_value_tier,
                    available_tracks=tuple(available_tracks),
                    reason="available Spotify tracks found before album release",
                )

            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
                artist_name=artist_name,
                project_title=project_title,
                spotify_artist=artist,
                artist_similarity=artist_similarity,
                artist_value_tier=artist_value_tier,
                reason="no confident Spotify album/project match",
            )

        album_kind = _classify_album_kind(album, self._config.ep_min_track_count)
        release_date = _parse_release_date(str(album["release_date"]))
        release_delta = (release_date - active_today).days
        if album_kind in {"album", "ep"} and release_delta <= 0:
            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ALBUM_CONFIRMED,
                artist_name=artist_name,
                project_title=project_title,
                spotify_artist=artist,
                spotify_album=album,
                artist_similarity=artist_similarity,
                title_similarity=title_similarity,
                album_kind=album_kind,
                artist_value_tier=artist_value_tier,
            )

        if album_kind in {"album", "ep"} and release_delta > 0:
            return SpotifyMatchResult(
                status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
                artist_name=artist_name,
                project_title=project_title,
                spotify_artist=artist,
                spotify_album=album,
                artist_similarity=artist_similarity,
                title_similarity=title_similarity,
                album_kind=album_kind,
                artist_value_tier=artist_value_tier,
                reason=(
                    "Spotify has a future-dated album/EP page, but official "
                    "track URIs are required for prerelease playlist eligibility"
                ),
            )

        return SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name=artist_name,
            project_title=project_title,
            spotify_artist=artist,
            spotify_album=album,
            artist_similarity=artist_similarity,
            title_similarity=title_similarity,
            album_kind=album_kind,
            artist_value_tier=artist_value_tier,
            reason="matched Spotify result is not a valid album/EP opportunity",
        )

    def _best_artist_match(self, artist_name: str) -> tuple[Mapping[str, Any] | None, float]:
        candidates = self._client.search_artists(
            artist_name,
            limit=5,
            market=self._config.market,
        )
        scored = [
            (candidate, _similarity(artist_name, str(candidate.get("name", ""))))
            for candidate in candidates
        ]
        if not scored:
            return None, 0.0

        best_artist, best_score = max(scored, key=lambda item: item[1])
        if best_score < self._config.min_artist_similarity:
            return None, best_score
        return best_artist, best_score

    def _best_album_match(
        self,
        *,
        artist_name: str,
        project_title: str,
        spotify_artist: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any] | None, float]:
        query = f'album:"{project_title}" artist:"{artist_name}"'
        candidates = self._client.search_albums(
            query,
            limit=self._config.search_limit,
            market=self._config.market,
        )
        scored: list[tuple[Mapping[str, Any], float]] = []
        for candidate in candidates:
            title_score = _similarity(project_title, str(candidate.get("name", "")))
            artist_score = _album_artist_similarity(candidate, spotify_artist, artist_name)
            if (
                title_score >= self._config.min_title_similarity
                and artist_score >= self._config.min_artist_similarity
            ):
                scored.append((candidate, title_score))

        if not scored:
            return None, 0.0

        return max(scored, key=lambda item: item[1])

    def _available_pre_release_tracks(
        self,
        *,
        event: NormalizedEvent,
        artist_name: str,
        project_title: str,
        spotify_artist: Mapping[str, Any],
        today: date,
    ) -> list[Mapping[str, Any]]:
        query = f'artist:"{artist_name}" "{project_title}"'
        candidates = self._client.search_tracks(
            query,
            limit=self._config.search_limit,
            market=self._config.market,
        )

        tracks: list[Mapping[str, Any]] = []
        seen_ids: set[str] = set()
        for candidate in candidates:
            if not _track_matches_project(candidate, spotify_artist, project_title, self._config):
                continue
            track_id = str(candidate.get("id", ""))
            if track_id in seen_ids:
                continue
            seen_ids.add(track_id)
            tracks.append(candidate)

        if tracks:
            return tracks

        return self._recent_artist_release_tracks(
            event=event,
            spotify_artist=spotify_artist,
            today=today,
        )

    def _recent_artist_release_tracks(
        self,
        *,
        event: NormalizedEvent,
        spotify_artist: Mapping[str, Any],
        today: date,
    ) -> list[Mapping[str, Any]]:
        artist_id = str(spotify_artist.get("id", "")).strip()
        if not artist_id or event.event_date is None:
            return []

        posted_tracks = event.raw_payload.get("genius_posted_tracks")
        if not isinstance(posted_tracks, int) or posted_tracks <= 0:
            return []

        releases = self._client.get_artist_albums(
            artist_id,
            market=self._config.market,
            limit=50,
        )
        eligible: list[Mapping[str, Any]] = []
        for release in releases:
            release_id = str(release.get("id", "")).strip()
            if not release_id:
                continue
            release_kind = _classify_album_kind(release, self._config.ep_min_track_count)
            if release_kind != "single":
                continue
            release_date_raw = str(release.get("release_date", "")).strip()
            if not release_date_raw:
                continue
            try:
                release_date = _parse_release_date(release_date_raw)
            except ValueError:
                continue
            if release_date > today or release_date >= event.event_date:
                continue
            if (event.event_date - release_date).days > self._config.recent_single_lookback_days:
                continue
            eligible.append(release)

        eligible.sort(key=lambda item: str(item.get("release_date", "")), reverse=True)

        tracks: list[Mapping[str, Any]] = []
        seen_ids: set[str] = set()
        for release in eligible:
            release_id = str(release.get("id", "")).strip()
            for track in self._client.get_album_tracks(release_id, market=self._config.market):
                track_id = str(track.get("id", "")).strip()
                if not track_id or track_id in seen_ids:
                    continue
                if not _track_matches_artist(track, spotify_artist):
                    continue
                seen_ids.add(track_id)
                payload = dict(track)
                payload.setdefault(
                    "album",
                    {
                        "id": release.get("id"),
                        "name": release.get("name"),
                        "release_date": release.get("release_date"),
                        "album_type": release.get("album_type"),
                        "uri": release.get("uri"),
                    },
                )
                tracks.append(payload)
        return tracks


def _is_upcoming_release(
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


def _project_title_from_event(event: NormalizedEvent) -> str:
    raw_title = event.raw_payload.get("project_title")
    if isinstance(raw_title, str) and raw_title.strip():
        return raw_title.strip()

    if event.artist_names and event.title.startswith(f"{event.artist_names[0]} - "):
        return event.title[len(event.artist_names[0]) + 3 :].strip()
    return event.title.strip()


def _artist_value_tier(
    artist: Mapping[str, Any],
    config: SpotifyMatcherConfig,
) -> ArtistValueTier:
    popularity = artist.get("popularity")
    followers = artist.get("followers")
    follower_count = followers.get("total") if isinstance(followers, dict) else None

    if isinstance(popularity, int) and popularity >= config.high_value_popularity:
        return ArtistValueTier.HIGH

    if (
        isinstance(popularity, int)
        and popularity >= config.review_value_popularity
        and isinstance(follower_count, int)
        and follower_count >= config.review_value_followers
    ):
        return ArtistValueTier.REVIEW

    if isinstance(follower_count, int) and follower_count >= config.legacy_review_followers:
        return ArtistValueTier.REVIEW

    return ArtistValueTier.LOW


def _album_artist_similarity(
    album: Mapping[str, Any],
    spotify_artist: Mapping[str, Any],
    fallback_artist_name: str,
) -> float:
    expected_id = spotify_artist.get("id")
    artists = album.get("artists", [])
    if isinstance(artists, list):
        for artist in artists:
            if isinstance(artist, dict) and expected_id and artist.get("id") == expected_id:
                return 1.0
        return max(
            (
                _similarity(fallback_artist_name, str(artist.get("name", "")))
                for artist in artists
                if isinstance(artist, dict)
            ),
            default=0.0,
        )
    return 0.0


def _track_matches_artist(
    track: Mapping[str, Any],
    spotify_artist: Mapping[str, Any],
) -> bool:
    artists = track.get("artists", [])
    expected_artist_id = spotify_artist.get("id")
    if not isinstance(artists, list):
        return False
    return any(
        isinstance(artist, dict)
        and expected_artist_id
        and artist.get("id") == expected_artist_id
        for artist in artists
    )


def _track_matches_project(
    track: Mapping[str, Any],
    spotify_artist: Mapping[str, Any],
    project_title: str,
    config: SpotifyMatcherConfig,
) -> bool:
    if not _track_matches_artist(track, spotify_artist):
        return False

    album = track.get("album")
    if not isinstance(album, dict):
        return False

    album_name = str(album.get("name", ""))
    title_similarity = _similarity(project_title, album_name)
    return title_similarity >= config.min_title_similarity


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )


def _artist_payload(artist: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if artist is None:
        return None

    followers = artist.get("followers")
    return {
        "id": artist.get("id"),
        "name": artist.get("name"),
        "popularity": artist.get("popularity"),
        "followers": followers.get("total") if isinstance(followers, dict) else None,
        "uri": artist.get("uri"),
    }


def _album_payload(album: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if album is None:
        return None

    return {
        "id": album.get("id"),
        "name": album.get("name"),
        "album_type": album.get("album_type"),
        "release_date": album.get("release_date"),
        "release_date_precision": album.get("release_date_precision"),
        "total_tracks": album.get("total_tracks"),
        "uri": album.get("uri"),
    }


def _track_payload(track: Mapping[str, Any]) -> dict[str, Any]:
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    return {
        "id": track.get("id"),
        "name": track.get("name"),
        "uri": track.get("uri"),
        "album_id": album.get("id") if isinstance(album, dict) else None,
        "album_name": album.get("name") if isinstance(album, dict) else None,
        "release_date": album.get("release_date") if isinstance(album, dict) else None,
    }
