"""Resolve scraped Spotify prerelease tracks to official Spotify track objects."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol

from playlist_builder.spotify_album_watcher import SpotifyWebAPIClient
from playlist_builder.spotify_ui_scraper import (
    SpotifyPrereleaseSnapshot,
    SpotifyPrereleaseTrack,
)


class SpotifyTrackSearchClient(Protocol):
    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """Search Spotify tracks."""


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseResolverConfig:
    market: str | None = None
    search_limit: int = 10
    min_title_similarity: float = 0.86
    min_artist_similarity: float = 0.82
    duration_tolerance_seconds: int = 3

    def __post_init__(self) -> None:
        if self.search_limit <= 0:
            raise ValueError("search_limit must be positive")
        if not 0 <= self.min_title_similarity <= 1:
            raise ValueError("min_title_similarity must be between 0 and 1")
        if not 0 <= self.min_artist_similarity <= 1:
            raise ValueError("min_artist_similarity must be between 0 and 1")
        if self.duration_tolerance_seconds < 0:
            raise ValueError("duration_tolerance_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class ResolvedPrereleaseTrack:
    source_track: SpotifyPrereleaseTrack
    spotify_track: Mapping[str, Any] | None
    query: str
    title_similarity: float = 0.0
    artist_similarity: float = 0.0
    duration_delta_seconds: int | None = None
    reason: str | None = None

    @property
    def matched(self) -> bool:
        return self.spotify_track is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_track": self.source_track.to_dict(),
            "spotify_track": _track_payload(self.spotify_track),
            "query": self.query,
            "title_similarity": round(self.title_similarity, 3),
            "artist_similarity": round(self.artist_similarity, 3),
            "duration_delta_seconds": self.duration_delta_seconds,
            "matched": self.matched,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseResolution:
    prerelease: SpotifyPrereleaseSnapshot
    tracks: tuple[ResolvedPrereleaseTrack, ...]

    @property
    def matched_tracks(self) -> tuple[ResolvedPrereleaseTrack, ...]:
        return tuple(track for track in self.tracks if track.matched)

    @property
    def unmatched_tracks(self) -> tuple[ResolvedPrereleaseTrack, ...]:
        return tuple(track for track in self.tracks if not track.matched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prerelease": self.prerelease.to_dict(),
            "matched_count": len(self.matched_tracks),
            "unmatched_count": len(self.unmatched_tracks),
            "tracks": [track.to_dict() for track in self.tracks],
        }


class SpotifyPrereleaseTrackResolver:
    def __init__(
        self,
        client: SpotifyTrackSearchClient,
        config: SpotifyPrereleaseResolverConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or SpotifyPrereleaseResolverConfig()

    @classmethod
    def from_env(
        cls,
        config: SpotifyPrereleaseResolverConfig | None = None,
    ) -> "SpotifyPrereleaseTrackResolver":
        return cls(SpotifyWebAPIClient.from_env(), config=config)

    def resolve(self, snapshot: SpotifyPrereleaseSnapshot) -> SpotifyPrereleaseResolution:
        resolved = tuple(
            self.resolve_track(track, snapshot=snapshot)
            for track in snapshot.available_tracks
        )
        return SpotifyPrereleaseResolution(prerelease=snapshot, tracks=resolved)

    def resolve_track(
        self,
        track: SpotifyPrereleaseTrack,
        *,
        snapshot: SpotifyPrereleaseSnapshot,
    ) -> ResolvedPrereleaseTrack:
        query_artist = _query_artist(track, snapshot)
        query = f'track:"{track.title}" artist:"{query_artist}"'
        candidates = self._client.search_tracks(
            query,
            limit=self._config.search_limit,
            market=self._config.market,
        )

        scored = [
            self._score_candidate(track, candidate)
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
        if not scored:
            return ResolvedPrereleaseTrack(
                source_track=track,
                spotify_track=None,
                query=query,
                reason="Spotify search returned no track candidates",
            )

        best = max(scored, key=lambda item: item[0])
        _, candidate, title_score, artist_score, duration_delta = best
        if title_score < self._config.min_title_similarity:
            return ResolvedPrereleaseTrack(
                source_track=track,
                spotify_track=None,
                query=query,
                title_similarity=title_score,
                artist_similarity=artist_score,
                duration_delta_seconds=duration_delta,
                reason="best candidate title was not similar enough",
            )
        if artist_score < self._config.min_artist_similarity:
            return ResolvedPrereleaseTrack(
                source_track=track,
                spotify_track=None,
                query=query,
                title_similarity=title_score,
                artist_similarity=artist_score,
                duration_delta_seconds=duration_delta,
                reason="best candidate artist was not similar enough",
            )
        if (
            duration_delta is not None
            and duration_delta > self._config.duration_tolerance_seconds
        ):
            return ResolvedPrereleaseTrack(
                source_track=track,
                spotify_track=None,
                query=query,
                title_similarity=title_score,
                artist_similarity=artist_score,
                duration_delta_seconds=duration_delta,
                reason="best candidate duration differed too much",
            )

        return ResolvedPrereleaseTrack(
            source_track=track,
            spotify_track=candidate,
            query=query,
            title_similarity=title_score,
            artist_similarity=artist_score,
            duration_delta_seconds=duration_delta,
            reason="confident Spotify track match",
        )

    def _score_candidate(
        self,
        source: SpotifyPrereleaseTrack,
        candidate: Mapping[str, Any],
    ) -> tuple[float, Mapping[str, Any], float, float, int | None]:
        title_score = _similarity(source.title, str(candidate.get("name", "")))
        artist_score = _candidate_artist_similarity(source.artist_names, candidate)
        duration_delta = _duration_delta_seconds(source.duration, candidate)
        duration_score = (
            1.0
            if duration_delta is None
            else max(0.0, 1.0 - (duration_delta / 30))
        )
        score = (title_score * 0.58) + (artist_score * 0.32) + (duration_score * 0.10)
        return score, candidate, title_score, artist_score, duration_delta


def _query_artist(
    track: SpotifyPrereleaseTrack,
    snapshot: SpotifyPrereleaseSnapshot,
) -> str:
    if track.artist_names:
        return track.artist_names[0]
    return snapshot.artist_name or ""


def _candidate_artist_similarity(
    expected_names: tuple[str, ...],
    candidate: Mapping[str, Any],
) -> float:
    artists = candidate.get("artists", [])
    if not isinstance(artists, list):
        return 0.0

    candidate_names = [
        str(artist.get("name", ""))
        for artist in artists
        if isinstance(artist, Mapping)
    ]
    if not candidate_names:
        return 0.0

    expected = expected_names or tuple(candidate_names[:1])
    return max(
        (_similarity(expected_name, candidate_name) for expected_name in expected for candidate_name in candidate_names),
        default=0.0,
    )


def _duration_delta_seconds(
    source_duration: str | None,
    candidate: Mapping[str, Any],
) -> int | None:
    source_seconds = _duration_to_seconds(source_duration)
    duration_ms = candidate.get("duration_ms")
    if source_seconds is None or not isinstance(duration_ms, int):
        return None
    return abs(source_seconds - round(duration_ms / 1000))


def _duration_to_seconds(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    minutes, seconds = value.split(":", 1)
    if not minutes.isdigit() or not seconds.isdigit():
        return None
    return (int(minutes) * 60) + int(seconds)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )


def _track_payload(track: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if track is None:
        return None

    album = track.get("album") if isinstance(track.get("album"), Mapping) else {}
    artists = track.get("artists", [])
    return {
        "id": track.get("id"),
        "name": track.get("name"),
        "uri": track.get("uri"),
        "duration_ms": track.get("duration_ms"),
        "artists": [
            {"id": artist.get("id"), "name": artist.get("name"), "uri": artist.get("uri")}
            for artist in artists
            if isinstance(artist, Mapping)
        ]
        if isinstance(artists, list)
        else [],
        "album": {
            "id": album.get("id") if isinstance(album, Mapping) else None,
            "name": album.get("name") if isinstance(album, Mapping) else None,
            "uri": album.get("uri") if isinstance(album, Mapping) else None,
            "release_date": album.get("release_date") if isinstance(album, Mapping) else None,
        },
        "external_url": (
            track.get("external_urls", {}).get("spotify")
            if isinstance(track.get("external_urls"), Mapping)
            else None
        ),
    }
