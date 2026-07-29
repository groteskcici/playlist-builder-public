"""setlist.fm REST API client."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from playlist_builder.spotify_album_watcher import load_spotify_env_file


SETLIST_FM_API_BASE_URL = "https://api.setlist.fm/rest/1.0"
DEFAULT_PAGE_SIZE = 20
DEFAULT_SEARCH_MAX_PAGES = 5
DEFAULT_REQUEST_INTERVAL_SECONDS = 2.5
DEFAULT_429_RETRY_SECONDS = 15.0
MAX_429_RETRIES = 1


class SetlistFmError(RuntimeError):
    """Raised when setlist.fm returns an unusable response."""


@dataclass(frozen=True, slots=True)
class SetlistFmPage:
    items: tuple[Mapping[str, Any], ...]
    total: int
    page: int
    items_per_page: int

    @property
    def has_next(self) -> bool:
        if self.total <= 0:
            return False
        return self.page * self.items_per_page < self.total


class SetlistFmClient:
    def __init__(
        self,
        api_key: str,
        *,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        if not api_key.strip():
            raise ValueError("setlist.fm API key must not be empty")
        self._api_key = api_key.strip()
        self._request_interval_seconds = request_interval_seconds
        self._last_request_at: float | None = None

    @classmethod
    def from_env(cls) -> "SetlistFmClient":
        _load_env_files()
        api_key = os.environ.get("SETLISTFM_API_KEY")
        if not api_key:
            raise RuntimeError("SETLISTFM_API_KEY must be set")
        interval_raw = os.environ.get(
            "SETLISTFM_REQUEST_INTERVAL_SECONDS",
            str(DEFAULT_REQUEST_INTERVAL_SECONDS),
        )
        try:
            interval = float(interval_raw)
        except ValueError as exc:
            raise RuntimeError(
                "SETLISTFM_REQUEST_INTERVAL_SECONDS must be a number"
            ) from exc
        return cls(api_key, request_interval_seconds=interval)

    def search_artists(
        self,
        *,
        artist_name: str,
        sort: str | None = None,
        page: int = 1,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {
            "artistName": artist_name,
            "p": page,
        }
        if sort:
            params["sort"] = sort
        payload = self._get_json("/search/artists", params)
        return _list_field(payload, "artist")

    def search_setlists_page(
        self,
        *,
        artist_mbid: str | None = None,
        artist_name: str | None = None,
        city_name: str | None = None,
        country_code: str | None = None,
        venue_name: str | None = None,
        tour_name: str | None = None,
        date: str | None = None,
        year: int | None = None,
        page: int = 1,
    ) -> SetlistFmPage:
        params: dict[str, str | int] = {"p": page}
        if artist_mbid:
            params["artistMbid"] = artist_mbid
        if artist_name:
            params["artistName"] = artist_name
        if city_name:
            params["cityName"] = city_name
        if country_code:
            params["countryCode"] = country_code
        if venue_name:
            params["venueName"] = venue_name
        if tour_name:
            params["tourName"] = tour_name
        if date:
            params["date"] = date
        if year is not None:
            params["year"] = year

        payload = self._get_json("/search/setlists", params)
        items = tuple(_list_field(payload, "setlist"))
        total = int(payload.get("total") or len(items))
        items_per_page = int(payload.get("itemsPerPage") or DEFAULT_PAGE_SIZE)
        current_page = int(payload.get("page") or page)
        return SetlistFmPage(
            items=items,
            total=total,
            page=current_page,
            items_per_page=items_per_page,
        )

    def search_setlists(
        self,
        *,
        artist_mbid: str | None = None,
        artist_name: str | None = None,
        city_name: str | None = None,
        country_code: str | None = None,
        venue_name: str | None = None,
        tour_name: str | None = None,
        date: str | None = None,
        year: int | None = None,
        page: int = 1,
    ) -> tuple[list[Mapping[str, Any]], int]:
        result = self.search_setlists_page(
            artist_mbid=artist_mbid,
            artist_name=artist_name,
            city_name=city_name,
            country_code=country_code,
            venue_name=venue_name,
            tour_name=tour_name,
            date=date,
            year=year,
            page=page,
        )
        return list(result.items), result.total

    def iter_search_setlists(
        self,
        *,
        artist_mbid: str | None = None,
        artist_name: str | None = None,
        city_name: str | None = None,
        country_code: str | None = None,
        venue_name: str | None = None,
        tour_name: str | None = None,
        date: str | None = None,
        year: int | None = None,
        max_pages: int = DEFAULT_SEARCH_MAX_PAGES,
    ) -> Iterator[Mapping[str, Any]]:
        page_number = 1
        while page_number <= max_pages:
            try:
                page = self.search_setlists_page(
                    artist_mbid=artist_mbid,
                    artist_name=artist_name,
                    city_name=city_name,
                    country_code=country_code,
                    venue_name=venue_name,
                    tour_name=tour_name,
                    date=date,
                    year=year,
                    page=page_number,
                )
            except SetlistFmError as exc:
                message = str(exc)
                if page_number == 1 and "HTTP 404" in message:
                    return
                if page_number > 1 and (
                    "page does not exist" in message or "HTTP 404" in message
                ):
                    return
                raise
            if not page.items:
                break
            yield from page.items
            if not page.has_next:
                break
            page_number += 1

    def get_artist_setlists_page(
        self,
        artist_mbid: str,
        *,
        page: int = 1,
    ) -> SetlistFmPage:
        payload = self._get_json(f"/artist/{artist_mbid}/setlists", {"p": page})
        items = tuple(_list_field(payload, "setlist"))
        total = int(payload.get("total") or len(items))
        items_per_page = int(payload.get("itemsPerPage") or DEFAULT_PAGE_SIZE)
        current_page = int(payload.get("page") or page)
        return SetlistFmPage(
            items=items,
            total=total,
            page=current_page,
            items_per_page=items_per_page,
        )

    def get_artist_setlists(
        self,
        artist_mbid: str,
        *,
        page: int = 1,
    ) -> tuple[list[Mapping[str, Any]], int]:
        result = self.get_artist_setlists_page(artist_mbid, page=page)
        return list(result.items), result.total

    def iter_artist_setlists(
        self,
        artist_mbid: str,
        *,
        max_pages: int = DEFAULT_SEARCH_MAX_PAGES,
    ) -> Iterator[Mapping[str, Any]]:
        page_number = 1
        while page_number <= max_pages:
            page = self.get_artist_setlists_page(artist_mbid, page=page_number)
            if not page.items:
                break
            yield from page.items
            if not page.has_next:
                break
            page_number += 1

    def get_setlist(self, setlist_id: str) -> Mapping[str, Any]:
        payload = self._get_json(f"/setlist/{setlist_id}", {})
        if not isinstance(payload, Mapping):
            raise SetlistFmError("setlist response was not an object")
        return payload

    def _get_json(
        self,
        path: str,
        params: Mapping[str, str | int],
        *,
        _attempt: int = 0,
    ) -> Mapping[str, Any]:
        self._throttle()
        query = urlencode(params) if params else ""
        url = f"{SETLIST_FM_API_BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "x-api-key": self._api_key,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and _attempt < MAX_429_RETRIES:
                time.sleep(_retry_after_seconds(exc.headers))
                return self._get_json(path, params, _attempt=_attempt + 1)
            if exc.code == 429:
                raise SetlistFmError(f"setlist.fm rate limit exceeded: {body}") from exc
            raise SetlistFmError(f"setlist.fm HTTP {exc.code}: {body}") from exc
        except OSError as exc:
            raise SetlistFmError(f"setlist.fm request failed: {exc}") from exc

        if not isinstance(payload, Mapping):
            raise SetlistFmError("setlist.fm response was not a JSON object")
        return payload

    def _throttle(self) -> None:
        if self._request_interval_seconds <= 0:
            return
        if self._last_request_at is None:
            self._last_request_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._request_interval_seconds:
            time.sleep(self._request_interval_seconds - elapsed)
        self._last_request_at = time.monotonic()


def _retry_after_seconds(headers: Any) -> float:
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is not None:
        try:
            return max(float(raw), 1.0)
        except (TypeError, ValueError):
            pass
    return DEFAULT_429_RETRY_SECONDS


def _list_field(payload: Mapping[str, Any], field_name: str) -> list[Mapping[str, Any]]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise SetlistFmError(f"expected {field_name} to be a list")
    return [item for item in value if isinstance(item, Mapping)]


def _load_env_files() -> None:
    project_root = Path(__file__).resolve().parents[3]
    seen: set[Path] = set()
    for candidate in (Path(".env"), project_root / ".env"):
        env_path = candidate.resolve()
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        load_spotify_env_file(env_path)
