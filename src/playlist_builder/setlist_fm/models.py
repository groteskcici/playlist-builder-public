"""Parsed setlist.fm models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ParsedSetlistSong:
    name: str
    info: str | None = None
    tape: bool = False
    with_artist: str | None = None
    cover_artist: str | None = None
    set_name: str | None = None
    encore: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "info": self.info,
            "tape": self.tape,
            "with_artist": self.with_artist,
            "cover_artist": self.cover_artist,
            "set_name": self.set_name,
            "encore": self.encore,
        }


@dataclass(frozen=True, slots=True)
class ParsedSetlist:
    setlist_id: str
    version_id: str | None
    artist_name: str
    artist_mbid: str | None
    tour_name: str | None
    venue_name: str
    venue_id: str | None
    city_name: str | None
    country_code: str | None
    event_date: date
    url: str | None
    songs: tuple[ParsedSetlistSong, ...]

    @property
    def live_songs(self) -> tuple[ParsedSetlistSong, ...]:
        return tuple(song for song in self.songs if not song.tape)

    @property
    def song_count(self) -> int:
        return len(self.songs)

    @property
    def live_song_count(self) -> int:
        return len(self.live_songs)

    def to_dict(self) -> dict[str, object]:
        return {
            "setlist_id": self.setlist_id,
            "version_id": self.version_id,
            "artist_name": self.artist_name,
            "artist_mbid": self.artist_mbid,
            "tour_name": self.tour_name,
            "venue_name": self.venue_name,
            "venue_id": self.venue_id,
            "city_name": self.city_name,
            "country_code": self.country_code,
            "event_date": self.event_date.isoformat(),
            "url": self.url,
            "song_count": self.song_count,
            "live_song_count": self.live_song_count,
            "songs": [song.to_dict() for song in self.songs],
        }
