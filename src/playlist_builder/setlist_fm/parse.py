"""Parse setlist.fm API payloads."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.setlist_fm.models import ParsedSetlist, ParsedSetlistSong


def parse_setlist(raw: Mapping[str, Any]) -> ParsedSetlist:
    event_date = _parse_event_date(raw.get("eventDate"))
    artist = raw.get("artist") if isinstance(raw.get("artist"), Mapping) else {}
    venue = raw.get("venue") if isinstance(raw.get("venue"), Mapping) else {}
    city = venue.get("city") if isinstance(venue.get("city"), Mapping) else {}
    country = city.get("country") if isinstance(city.get("country"), Mapping) else {}
    tour = raw.get("tour") if isinstance(raw.get("tour"), Mapping) else {}

    setlist_id = _required_str(raw.get("id"), "id")
    artist_name = _required_str(artist.get("name"), "artist.name")

    return ParsedSetlist(
        setlist_id=setlist_id,
        version_id=_optional_str(raw.get("versionId")),
        artist_name=artist_name,
        artist_mbid=_optional_str(artist.get("mbid")),
        tour_name=_optional_str(tour.get("name")),
        venue_name=_required_str(venue.get("name"), "venue.name"),
        venue_id=_optional_str(venue.get("id")),
        city_name=_optional_str(city.get("name")),
        country_code=_optional_str(country.get("code")),
        event_date=event_date,
        url=_optional_str(raw.get("url")),
        songs=tuple(_parse_songs(raw)),
    )


def setlist_to_event(
    parsed: ParsedSetlist,
    *,
    scope: str = "show",
    tour_key: str | None = None,
) -> NormalizedEvent:
    year = parsed.event_date.year
    tour = parsed.tour_name or "Tour"
    if scope == "tour":
        title = f"{parsed.artist_name} {year} {tour} setlist"
        search_terms = (
            f"{parsed.artist_name} {year} tour setlist",
            f"{parsed.artist_name} {tour} setlist",
        )
    else:
        location = parsed.city_name or parsed.venue_name
        title = f"{parsed.artist_name} {year} {tour} setlist ({location})"
        search_terms = (
            f"{parsed.artist_name} {year} tour setlist",
            f"{parsed.artist_name} setlist {location} {parsed.event_date.isoformat()}",
        )

    return NormalizedEvent(
        event_type=EventType.SETLIST,
        source="setlist.fm",
        title=title,
        artist_names=(parsed.artist_name,),
        event_date=parsed.event_date,
        confidence=1.0,
        raw_payload=parsed.to_dict(),
        candidate_search_terms=search_terms,
        detected_at=datetime.now(timezone.utc),
        source_event_id=(
            parsed.setlist_id
            if scope == "show"
            else (tour_key or parsed.setlist_id)
        ),
    )


def parsed_setlist_from_dict(data: Mapping[str, Any]) -> ParsedSetlist:
    event_date_raw = data.get("event_date")
    if not isinstance(event_date_raw, str):
        raise ValueError("canonical_setlist event_date is required")
    event_date = date.fromisoformat(event_date_raw)

    songs_raw = data.get("songs")
    songs: tuple[ParsedSetlistSong, ...] = ()
    if isinstance(songs_raw, list):
        songs = tuple(
            ParsedSetlistSong(
                name=str(song.get("name", "")),
                info=_optional_str(song.get("info")) if isinstance(song, Mapping) else None,
                tape=bool(song.get("tape")) if isinstance(song, Mapping) else False,
                with_artist=_optional_str(song.get("with_artist")) if isinstance(song, Mapping) else None,
                cover_artist=_optional_str(song.get("cover_artist")) if isinstance(song, Mapping) else None,
                set_name=_optional_str(song.get("set_name")) if isinstance(song, Mapping) else None,
                encore=song.get("encore") if isinstance(song, Mapping) and isinstance(song.get("encore"), int) else None,
            )
            for song in songs_raw
            if isinstance(song, Mapping) and str(song.get("name", "")).strip()
        )

    return ParsedSetlist(
        setlist_id=_required_str(data.get("setlist_id"), "setlist_id"),
        version_id=_optional_str(data.get("version_id")),
        artist_name=_required_str(data.get("artist_name"), "artist_name"),
        artist_mbid=_optional_str(data.get("artist_mbid")),
        tour_name=_optional_str(data.get("tour_name")),
        venue_name=_required_str(data.get("venue_name"), "venue_name"),
        venue_id=_optional_str(data.get("venue_id")),
        city_name=_optional_str(data.get("city_name")),
        country_code=_optional_str(data.get("country_code")),
        event_date=event_date,
        url=_optional_str(data.get("url")),
        songs=songs,
    )


def format_setlist_date(value: date) -> str:
    """Format a date for setlist.fm search (dd-mm-yyyy)."""
    return value.strftime("%d-%m-%Y")


def _parse_songs(raw: Mapping[str, Any]) -> list[ParsedSetlistSong]:
    songs: list[ParsedSetlistSong] = []
    sets = raw.get("set")
    if not isinstance(sets, list):
        nested = raw.get("sets")
        if isinstance(nested, Mapping):
            sets = nested.get("set")

    if not isinstance(sets, list):
        return songs

    for set_block in sets:
        if not isinstance(set_block, Mapping):
            continue
        set_name = _optional_str(set_block.get("name"))
        encore = set_block.get("encore")
        encore_number = int(encore) if isinstance(encore, int) else None
        set_songs = set_block.get("song")
        if not isinstance(set_songs, list):
            continue
        for song in set_songs:
            if not isinstance(song, Mapping):
                continue
            name = _optional_str(song.get("name"))
            if not name:
                continue
            with_artist = song.get("with")
            cover_artist = song.get("cover")
            songs.append(
                ParsedSetlistSong(
                    name=name,
                    info=_optional_str(song.get("info")),
                    tape=bool(song.get("tape")),
                    with_artist=_artist_name(with_artist),
                    cover_artist=_artist_name(cover_artist),
                    set_name=set_name,
                    encore=encore_number,
                )
            )
    return songs


def _parse_event_date(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("setlist eventDate is required")
    try:
        day, month, year = value.strip().split("-")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid setlist eventDate: {value!r}") from exc


def _artist_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _optional_str(value.get("name"))
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_str(value: Any, field_name: str) -> str:
    text = _optional_str(value)
    if not text:
        raise ValueError(f"setlist {field_name} is required")
    return text
