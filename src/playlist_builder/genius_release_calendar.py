"""Genius album release calendar watcher."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from playlist_builder.genius_page_fetcher import fetch_genius_page_html

from playlist_builder.events import EventType, JSONValue, NormalizedEvent
from playlist_builder.spotify_album_watcher import load_spotify_env_file
from playlist_builder.watchers import WatcherContext, WatcherFailure, WatcherResult


GENIUS_API_BASE_URL = "https://api.genius.com"
GENIUS_USER_AGENT = "PlaylistBuilder/0.1"
CALENDAR_TITLE_TEMPLATE = "{month_name} {year} Album Release Calendar"
DATE_HEADING_RE = re.compile(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})$")
DATE_TOKEN_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})(?!\d)")
TRACK_PROGRESS_RE = re.compile(r"\s+-\s+(?P<posted>\d+)/(?P<total>\d+|\?)$")


@dataclass(frozen=True, slots=True)
class GeniusCredentials:
    access_token: str

    @classmethod
    def from_env(cls) -> "GeniusCredentials":
        load_spotify_env_file(Path(__file__).resolve().parents[2] / ".env")
        token = os.environ.get("GENIUS_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("GENIUS_ACCESS_TOKEN must be set")
        return cls(access_token=token)


class GeniusAPIError(RuntimeError):
    """Raised when Genius returns an unusable response."""


class GeniusReleaseCalendarClient:
    """Small Genius client for finding and fetching release calendar pages."""

    def __init__(
        self,
        credentials: GeniusCredentials,
        *,
        api_base_url: str = GENIUS_API_BASE_URL,
        user_agent: str = GENIUS_USER_AGENT,
    ) -> None:
        self._credentials = credentials
        self._api_base_url = api_base_url.rstrip("/")
        self._user_agent = user_agent

    @classmethod
    def from_env(cls) -> "GeniusReleaseCalendarClient":
        return cls(GeniusCredentials.from_env())

    def find_calendar_page(self, *, month_name: str, year: int) -> Mapping[str, Any]:
        query = CALENDAR_TITLE_TEMPLATE.format(month_name=month_name, year=year)
        response = self._get_json(f"/search?{urlencode({'q': query})}")
        hits = response.get("response", {}).get("hits", [])
        if not isinstance(hits, list):
            raise GeniusAPIError("Genius search hits were not a list")

        expected = query.lower()
        for hit in hits:
            result = hit.get("result", {}) if isinstance(hit, dict) else {}
            title = str(result.get("title", "")).lower()
            primary_artist = str(result.get("primary_artist", {}).get("name", ""))
            if expected == title and primary_artist.lower() == "genius":
                return result

        raise GeniusAPIError(f"Could not find Genius calendar page for {query}")

    def fetch_html(self, url: str) -> str:
        html, _fetcher = fetch_genius_page_html(url)
        return html

    def _get_json(self, path: str) -> Mapping[str, Any]:
        request = Request(
            f"{self._api_base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._credentials.access_token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
            method="GET",
        )
        with urlopen(request, timeout=20) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise GeniusAPIError("Genius response was not a JSON object")
        return decoded


@dataclass(frozen=True, slots=True)
class GeniusReleaseCalendarConfig:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")


class GeniusReleaseCalendarWatcher:
    """Parse Genius's curated album release calendar into normalized events."""

    name = "genius_release_calendar"
    supported_event_types = (EventType.UPCOMING_ALBUM_CANDIDATE,)

    def __init__(
        self,
        client: GeniusReleaseCalendarClient,
        config: GeniusReleaseCalendarConfig,
    ) -> None:
        self._client = client
        self._config = config

    @classmethod
    def from_env(
        cls,
        config: GeniusReleaseCalendarConfig,
    ) -> "GeniusReleaseCalendarWatcher":
        return cls(GeniusReleaseCalendarClient.from_env(), config)

    def fetch(self, context: WatcherContext) -> WatcherResult:
        month_name = date(self._config.year, self._config.month, 1).strftime("%B")
        events: list[NormalizedEvent] = []
        failures: list[WatcherFailure] = []

        try:
            page = self._client.find_calendar_page(
                month_name=month_name,
                year=self._config.year,
            )
            html = self._client.fetch_html(str(page["url"]))
            parse_failures: list[str] = []
            entries = parse_calendar_entries(
                html,
                year=self._config.year,
                month=self._config.month,
                failures=parse_failures,
            )
            for message in parse_failures:
                failures.append(
                    WatcherFailure(
                        watcher_name=self.name,
                        message=message,
                    )
                )
        except Exception as exc:
            return WatcherResult(
                watcher_name=self.name,
                failures=(
                    WatcherFailure(
                        watcher_name=self.name,
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                ),
            )

        for entry in entries:
            try:
                if context.since and entry.release_date < context.since.date():
                    continue
                events.append(_entry_to_event(entry))
            except (TypeError, ValueError) as exc:
                failures.append(
                    WatcherFailure(
                        watcher_name=self.name,
                        message=f"Skipped malformed Genius calendar entry: {exc}",
                    )
                )

        return WatcherResult(
            watcher_name=self.name,
            events=tuple(events[: context.limit] if context.limit else events),
            failures=tuple(failures),
        )


@dataclass(frozen=True, slots=True)
class GeniusCalendarEntry:
    release_date: date
    artist_name: str
    project_title: str
    posted_tracks: int | None = None
    total_tracks: int | None = None


def parse_calendar_entries(
    html: str,
    *,
    year: int,
    month: int,
    failures: list[str] | None = None,
) -> list[GeniusCalendarEntry]:
    lines = _extract_lyrics_lines(html)
    entries: list[GeniusCalendarEntry] = []
    current_date: date | None = None

    for line in lines:
        try:
            heading = DATE_HEADING_RE.match(line)
            if heading:
                current_date = _calendar_date(
                    year,
                    int(heading.group("month")),
                    int(heading.group("day")),
                )
                continue
            if " - " not in line:
                date_tokens = list(DATE_TOKEN_RE.finditer(line))
                if date_tokens:
                    token = date_tokens[-1]
                    current_date = _calendar_date(
                        year,
                        int(token.group("month")),
                        int(token.group("day")),
                    )
                continue

            if current_date is None or current_date.month != month:
                continue

            entry = _parse_release_line(line, current_date)
            if entry:
                entries.append(entry)
        except (TypeError, ValueError) as exc:
            if failures is not None:
                failures.append(
                    f"Skipped unparseable Genius calendar line {line!r}: {exc}"
                )

    return entries


def _calendar_date(year: int, month: int, day: int) -> date:
    return date(year, month, day)


def _parse_release_line(line: str, release_date: date) -> GeniusCalendarEntry | None:
    if " - " not in line:
        return None

    clean_line = line.strip()
    posted_tracks: int | None = None
    total_tracks: int | None = None
    progress = TRACK_PROGRESS_RE.search(clean_line)
    if progress:
        try:
            posted_tracks = int(progress.group("posted"))
        except ValueError:
            posted_tracks = None
        total = progress.group("total")
        total_tracks = int(total) if total.isdigit() else None
        clean_line = clean_line[: progress.start()].strip()

    parts = clean_line.split(" - ", 1)
    if len(parts) != 2:
        return None

    artist_name, project_title = parts
    artist_name = artist_name.strip()
    project_title = project_title.strip()
    if not artist_name or not project_title:
        return None

    return GeniusCalendarEntry(
        release_date=release_date,
        artist_name=artist_name,
        project_title=project_title,
        posted_tracks=posted_tracks,
        total_tracks=total_tracks,
    )


def _entry_to_event(entry: GeniusCalendarEntry) -> NormalizedEvent:
    return NormalizedEvent(
        event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
        source="genius",
        title=f"{entry.artist_name} - {entry.project_title}",
        artist_names=(entry.artist_name,),
        event_date=entry.release_date,
        confidence=0.8,
        raw_payload=_entry_payload(entry),
        candidate_search_terms=(
            f"{entry.artist_name} {entry.project_title}",
            f"{entry.artist_name} {entry.project_title} full album",
            f"{entry.artist_name} {entry.release_date.year} album",
        ),
        source_event_id=f"{entry.release_date.isoformat()}:{entry.artist_name}:{entry.project_title}",
    )


def _entry_payload(entry: GeniusCalendarEntry) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "artist_name": entry.artist_name,
        "project_title": entry.project_title,
    }
    if entry.posted_tracks is not None:
        payload["genius_posted_tracks"] = entry.posted_tracks
    if entry.total_tracks is not None:
        payload["total_tracks"] = entry.total_tracks
    return payload


class _LyricsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("data-lyrics-container") == "true":
            self._depth += 1
        elif self._depth:
            self._depth += 1
        if self._depth and tag in {"br", "div", "h2", "h3", "li", "p"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.parts.append(data)


def _extract_lyrics_lines(html: str) -> list[str]:
    parser = _LyricsParser()
    parser.feed(html)
    return [line.strip() for line in "".join(parser.parts).splitlines() if line.strip()]
