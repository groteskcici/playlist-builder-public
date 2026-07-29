"""Resolve setlist.fm song names to Spotify track URIs."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol

from playlist_builder.setlist_fm.models import ParsedSetlist, ParsedSetlistSong


class SetlistTrackClient(Protocol):
    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """Search Spotify tracks."""


@dataclass(frozen=True, slots=True)
class ResolvedSetlistTrack:
    song_name: str
    spotify_track_id: str | None
    spotify_track_name: str | None
    spotify_artist_name: str | None
    uri: str | None
    match_similarity: float
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "song_name": self.song_name,
            "spotify_track_id": self.spotify_track_id,
            "spotify_track_name": self.spotify_track_name,
            "spotify_artist_name": self.spotify_artist_name,
            "uri": self.uri,
            "match_similarity": round(self.match_similarity, 4),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSetlist:
    setlist_id: str
    artist_name: str
    tracks: tuple[ResolvedSetlistTrack, ...]

    @property
    def resolved_count(self) -> int:
        return sum(1 for track in self.tracks if track.uri)

    def to_dict(self) -> dict[str, object]:
        return {
            "setlist_id": self.setlist_id,
            "artist_name": self.artist_name,
            "track_count": len(self.tracks),
            "resolved_count": self.resolved_count,
            "tracks": [track.to_dict() for track in self.tracks],
        }


class SetlistTrackResolver:
    def __init__(
        self,
        client: SetlistTrackClient,
        *,
        market: str | None = None,
        min_similarity: float = 0.72,
        include_tape: bool = False,
    ) -> None:
        self._client = client
        self._market = market
        self._min_similarity = min_similarity
        self._include_tape = include_tape

    def resolve(self, setlist: ParsedSetlist) -> ResolvedSetlist:
        songs = setlist.songs if self._include_tape else setlist.live_songs
        tracks = tuple(
            self._resolve_song(setlist.artist_name, song) for song in songs
        )
        return ResolvedSetlist(
            setlist_id=setlist.setlist_id,
            artist_name=setlist.artist_name,
            tracks=tracks,
        )

    def _resolve_song(
        self,
        artist_name: str,
        song: ParsedSetlistSong,
    ) -> ResolvedSetlistTrack:
        if song.tape and not self._include_tape:
            return ResolvedSetlistTrack(
                song_name=song.name,
                spotify_track_id=None,
                spotify_track_name=None,
                spotify_artist_name=None,
                uri=None,
                match_similarity=0.0,
                status="skipped_tape",
            )

        search_artist = song.cover_artist or artist_name
        query = f'track:"{song.name}" artist:"{search_artist}"'
        candidates = self._client.search_tracks(
            query,
            limit=8,
            market=self._market,
        )
        if not candidates:
            query = f'artist:"{search_artist}" "{song.name}"'
            candidates = self._client.search_tracks(
                query,
                limit=8,
                market=self._market,
            )

        best_track: Mapping[str, Any] | None = None
        best_score = 0.0
        for candidate in candidates:
            title_score = _similarity(song.name, str(candidate.get("name", "")))
            artist_score = _track_artist_score(search_artist, candidate)
            score = min(title_score, artist_score * 0.35 + title_score * 0.65)
            if score > best_score:
                best_score = score
                best_track = candidate

        if best_track is None or best_score < self._min_similarity:
            return ResolvedSetlistTrack(
                song_name=song.name,
                spotify_track_id=None,
                spotify_track_name=None,
                spotify_artist_name=None,
                uri=None,
                match_similarity=best_score,
                status="track_not_found",
            )

        artists = best_track.get("artists")
        primary_artist = None
        if isinstance(artists, list) and artists and isinstance(artists[0], Mapping):
            primary_artist = artists[0].get("name")

        return ResolvedSetlistTrack(
            song_name=song.name,
            spotify_track_id=_optional_str(best_track.get("id")),
            spotify_track_name=_optional_str(best_track.get("name")),
            spotify_artist_name=_optional_str(primary_artist),
            uri=_optional_str(best_track.get("uri")),
            match_similarity=best_score,
            status="resolved",
        )


def _track_artist_score(expected_artist: str, track: Mapping[str, Any]) -> float:
    artists = track.get("artists")
    if not isinstance(artists, list):
        return 0.0
    scores = [
        _similarity(expected_artist, str(artist.get("name", "")))
        for artist in artists
        if isinstance(artist, Mapping)
    ]
    return max(scores, default=0.0)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
