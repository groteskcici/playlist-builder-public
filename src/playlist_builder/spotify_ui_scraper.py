"""Spotify public UI scraping helpers for data missing from the official API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


ARTIST_ID_RE = re.compile(r"open\.spotify\.com/artist/([A-Za-z0-9]+)|spotify:artist:([A-Za-z0-9]+)")
MONTHLY_LISTENERS_RE = re.compile(
    r"(?P<count>[\d.,\s]+)\s*(?P<label>monthly listeners|monatliche hörer:innen|monatliche hörer)",
    re.IGNORECASE,
)
PRERELEASE_URI_RE = re.compile(r"spotify:prerelease:[A-Za-z0-9]+")
PRERELEASE_PATH_RE = re.compile(r"(?:href=\\\"|href=\"|/)(?:prerelease/)(?P<id>[A-Za-z0-9]+)")
COUNTDOWN_TITLE_RE = re.compile(
    r"Release countdown\s+(?P<title>.{1,140}?)\s+Album\b",
    re.IGNORECASE,
)
PRERELEASE_ID_RE = re.compile(r"open\.spotify\.com/prerelease/([A-Za-z0-9]+)|spotify:prerelease:([A-Za-z0-9]+)")
PRERELEASE_TITLE_RE = re.compile(
    r"(?P<title>.+?)\s+-\s+Upcoming Album by\s+(?P<artist>.+?)\s+\|\s+Spotify"
)
PRERELEASE_DATE_RE = re.compile(
    r"Releases on (?P<day>\d{1,2}) (?P<month>[A-Za-z]+) (?P<year>\d{4})"
)
DURATION_RE = re.compile(r"^\d+:\d{2}$|^-:--$")
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True, slots=True)
class SpotifyReleaseCountdown:
    title: str | None
    uri: str


@dataclass(frozen=True, slots=True)
class SpotifyArtistUiSnapshot:
    artist_id: str
    url: str
    monthly_listeners: int | None
    release_countdowns: tuple[SpotifyReleaseCountdown, ...]
    fetcher: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist_id": self.artist_id,
            "url": self.url,
            "monthly_listeners": self.monthly_listeners,
            "release_countdowns": [
                {"title": countdown.title, "uri": countdown.uri}
                for countdown in self.release_countdowns
            ],
            "fetcher": self.fetcher,
        }


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseTrack:
    position: int
    title: str
    artist_names: tuple[str, ...]
    duration: str | None
    is_available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "title": self.title,
            "artist_names": list(self.artist_names),
            "duration": self.duration,
            "is_available": self.is_available,
        }


@dataclass(frozen=True, slots=True)
class SpotifyPrereleaseSnapshot:
    prerelease_id: str
    url: str
    title: str | None
    artist_name: str | None
    release_date: date | None
    tracks: tuple[SpotifyPrereleaseTrack, ...]
    fetcher: str

    @property
    def available_tracks(self) -> tuple[SpotifyPrereleaseTrack, ...]:
        return tuple(track for track in self.tracks if track.is_available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prerelease_id": self.prerelease_id,
            "url": self.url,
            "title": self.title,
            "artist_name": self.artist_name,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "tracks": [track.to_dict() for track in self.tracks],
            "available_tracks": [track.to_dict() for track in self.available_tracks],
            "fetcher": self.fetcher,
        }


class SpotifyUiScrapeError(RuntimeError):
    """Raised when Spotify UI scraping fails."""


class SpotifyArtistUiScraper:
    """Fetch and parse public Spotify artist pages."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = 45_000) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms

    def scrape_artist(self, artist: str) -> SpotifyArtistUiSnapshot:
        artist_id = parse_artist_id(artist)
        url = f"https://open.spotify.com/artist/{artist_id}"
        html, text, fetcher = fetch_spotify_artist_page(
            url,
            headless=self._headless,
            timeout_ms=self._timeout_ms,
        )
        return parse_artist_ui_snapshot(
            artist_id=artist_id,
            url=url,
            html=html,
            text=text,
            fetcher=fetcher,
        )

    def scrape_prerelease(self, prerelease: str) -> SpotifyPrereleaseSnapshot:
        prerelease_id = parse_prerelease_id(prerelease)
        url = f"https://open.spotify.com/prerelease/{prerelease_id}"
        html, text, fetcher = fetch_spotify_artist_page(
            url,
            headless=self._headless,
            timeout_ms=self._timeout_ms,
        )
        return parse_prerelease_snapshot(
            prerelease_id=prerelease_id,
            url=url,
            text=text,
            fetcher=fetcher,
        )


def parse_artist_id(value: str) -> str:
    match = ARTIST_ID_RE.search(value)
    if match:
        return next(group for group in match.groups() if group)
    return value.strip()


def parse_prerelease_id(value: str) -> str:
    match = PRERELEASE_ID_RE.search(value)
    if match:
        return next(group for group in match.groups() if group)
    return value.strip()


def fetch_spotify_artist_page(
    url: str,
    *,
    headless: bool,
    timeout_ms: int,
) -> tuple[str, str, str]:
    try:
        from scrapling.fetchers import DynamicFetcher

        page = DynamicFetcher.fetch(
            url,
            headless=headless,
            network_idle=True,
            timeout=timeout_ms,
        )
        html = extract_html(page)
        text = extract_text(page, html)
        return html, text, "DynamicFetcher"
    except Exception as dynamic_error:
        try:
            from scrapling.fetchers import Fetcher

            page = Fetcher.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
                },
                timeout=timeout_ms / 1000,
            )
            html = extract_html(page)
            text = extract_text(page, html)
            return html, text, "Fetcher"
        except Exception as fallback_error:
            raise SpotifyUiScrapeError(
                f"Dynamic fetch failed: {dynamic_error}; fallback failed: {fallback_error}"
            ) from fallback_error


def parse_artist_ui_snapshot(
    *,
    artist_id: str,
    url: str,
    html: str,
    text: str,
    fetcher: str,
) -> SpotifyArtistUiSnapshot:
    combined = f"{text}\n{html}"
    prerelease_uris = _prerelease_uris(combined)
    titles = _countdown_titles(combined)

    release_countdowns = tuple(
        SpotifyReleaseCountdown(
            title=titles[index] if index < len(titles) else None,
            uri=uri,
        )
        for index, uri in enumerate(prerelease_uris)
    )

    return SpotifyArtistUiSnapshot(
        artist_id=artist_id,
        url=url,
        monthly_listeners=_monthly_listeners(combined),
        release_countdowns=release_countdowns,
        fetcher=fetcher,
    )


def parse_prerelease_snapshot(
    *,
    prerelease_id: str,
    url: str,
    text: str,
    fetcher: str,
) -> SpotifyPrereleaseSnapshot:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = " ".join(lines)
    title, artist_name = _prerelease_title_artist(lines, joined)

    return SpotifyPrereleaseSnapshot(
        prerelease_id=prerelease_id,
        url=url,
        title=title,
        artist_name=artist_name,
        release_date=_prerelease_release_date(joined),
        tracks=tuple(_prerelease_tracks(lines)),
        fetcher=fetcher,
    )


def extract_html(page: Any) -> str:
    for attr in ("html_content", "html", "body", "text"):
        value = getattr(page, attr, None)
        if callable(value):
            try:
                result = value()
            except TypeError:
                continue
            if isinstance(result, str):
                return result
            if isinstance(result, bytes):
                return result.decode("utf-8", errors="replace")
        elif isinstance(value, str):
            return value
        elif isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
    return str(page)


def extract_text(page: Any, html: str) -> str:
    for attr in ("get_all_text", "text", "body"):
        value = getattr(page, attr, None)
        if callable(value):
            try:
                result = value()
            except TypeError:
                continue
            if isinstance(result, str):
                return result
            if isinstance(result, bytes):
                return result.decode("utf-8", errors="replace")
        elif isinstance(value, str):
            return value
        elif isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
    return re.sub(r"<[^>]+>", " ", html)


def _monthly_listeners(value: str) -> int | None:
    match = MONTHLY_LISTENERS_RE.search(value)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group("count"))
    return int(digits) if digits else None


def _prerelease_uris(value: str) -> tuple[str, ...]:
    uris = set(PRERELEASE_URI_RE.findall(value))
    for match in PRERELEASE_PATH_RE.finditer(value):
        uris.add(f"spotify:prerelease:{match.group('id')}")
    return tuple(sorted(uris))


def _countdown_titles(value: str) -> tuple[str, ...]:
    titles = []
    for match in COUNTDOWN_TITLE_RE.finditer(value):
        title = " ".join(match.group("title").split())
        if title and title not in titles:
            titles.append(title)
    return tuple(titles)


def _prerelease_title_artist(
    lines: list[str],
    joined: str,
) -> tuple[str | None, str | None]:
    if lines:
        match = PRERELEASE_TITLE_RE.match(lines[0])
        if match:
            return match.group("title").strip(), match.group("artist").strip()

    # Fallback for rendered text where the title appears before "Album".
    try:
        album_index = lines.index("Album")
        title = lines[album_index - 1] if album_index > 0 else None
        after_album = lines[album_index + 1 :]
        artist = next((line.split(" • ", 1)[0] for line in after_album if " • Releases on " in line), None)
        return title, artist
    except ValueError:
        return None, None


def _prerelease_release_date(value: str) -> date | None:
    match = PRERELEASE_DATE_RE.search(value)
    if not match:
        return None

    month = MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    return date(int(match.group("year")), month, int(match.group("day")))


def _prerelease_tracks(lines: list[str]) -> list[SpotifyPrereleaseTrack]:
    try:
        index = lines.index("Tracklist preview") + 1
    except ValueError:
        return []

    tracks: list[SpotifyPrereleaseTrack] = []
    while index < len(lines):
        if not lines[index].isdigit():
            index += 1
            continue

        position = int(lines[index])
        if index + 2 >= len(lines):
            break

        title = lines[index + 1]
        index += 2
        artist_names: list[str] = []
        while index < len(lines) and not DURATION_RE.match(lines[index]):
            if lines[index].startswith("©") or lines[index].startswith("℗"):
                return tracks
            if lines[index] != "E":
                artist_names.append(lines[index])
            index += 1

        if index >= len(lines):
            break

        duration_value = lines[index]
        tracks.append(
            SpotifyPrereleaseTrack(
                position=position,
                title=title,
                artist_names=tuple(artist_names),
                duration=None if duration_value == "-:--" else duration_value,
                is_available=duration_value != "-:--",
            )
        )
        index += 1

    return tracks
