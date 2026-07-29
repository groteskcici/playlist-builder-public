"""Spotify Web API client and shared helpers."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE_URL = "https://accounts.spotify.com"
SPOTIFY_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
SPOTIFY_MAX_GET_RETRIES = 3


@dataclass(frozen=True, slots=True)
class SpotifyCredentials:
    """Spotify credentials loaded from environment variables."""

    client_id: str
    client_secret: str
    access_token: str | None = None
    refresh_token: str | None = None

    @classmethod
    def from_env(cls) -> "SpotifyCredentials":
        from playlist_builder.spotify_credentials import api_credentials_from_env

        return api_credentials_from_env()


def _load_env_files() -> None:
    """Load project .env files without overwriting existing environment variables."""

    project_root = Path(__file__).resolve().parents[2]
    seen: set[Path] = set()
    for candidate in (Path(".env"), project_root / ".env"):
        env_path = candidate.resolve()
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        load_spotify_env_file(env_path)


def load_spotify_env_file(path: str | Path) -> None:
    """Load KEY=VALUE lines from a .env file without overwriting env vars."""

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


class SpotifyAPIError(RuntimeError):
    """Raised when Spotify returns an unusable response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class SpotifyWebAPIClient:
    """Small dependency-free Spotify Web API client."""

    def __init__(
        self,
        credentials: SpotifyCredentials,
        *,
        api_base_url: str = SPOTIFY_API_BASE_URL,
        accounts_base_url: str = SPOTIFY_ACCOUNTS_BASE_URL,
    ) -> None:
        self._credentials = credentials
        self._api_base_url = api_base_url.rstrip("/")
        self._accounts_base_url = accounts_base_url.rstrip("/")
        self._access_token = credentials.access_token

    @classmethod
    def from_env(cls) -> "SpotifyWebAPIClient":
        from playlist_builder.spotify_credentials import api_credentials_from_env

        return cls(api_credentials_from_env())

    def get_artists(self, artist_ids: list[str]) -> Mapping[str, Mapping[str, Any]]:
        unique_ids = list(dict.fromkeys(artist_ids))
        artists: dict[str, Mapping[str, Any]] = {}

        for index in range(0, len(unique_ids), 50):
            batch = unique_ids[index : index + 50]
            if not batch:
                continue

            response = self._get_json(f"/artists?{urlencode({'ids': ','.join(batch)})}")
            for artist in response.get("artists", []):
                if artist and artist.get("id"):
                    artists[str(artist["id"])] = artist

        return artists

    def search_artists(
        self,
        query: str,
        *,
        limit: int = 5,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "type": "artist",
            "limit": min(limit, 50),
        }
        if market:
            params["market"] = market

        response = self._get_json(f"/search?{urlencode(params)}")
        items = response.get("artists", {}).get("items", [])
        return items if isinstance(items, list) else []

    def search_albums(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "type": "album",
            "limit": min(limit, 50),
        }
        if market:
            params["market"] = market

        response = self._get_json(f"/search?{urlencode(params)}")
        items = response.get("albums", {}).get("items", [])
        return items if isinstance(items, list) else []

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "type": "track",
            "limit": min(limit, 50),
        }
        if market:
            params["market"] = market

        response = self._get_json(f"/search?{urlencode(params)}")
        items = response.get("tracks", {}).get("items", [])
        return items if isinstance(items, list) else []

    def search_playlists(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "type": "playlist",
            "limit": min(limit, 50),
        }
        if market:
            params["market"] = market

        response = self._get_json(f"/search?{urlencode(params)}")
        items = response.get("playlists", {}).get("items", [])
        return items if isinstance(items, list) else []

    def get_current_user(self) -> Mapping[str, Any]:
        return self._get_json("/me")

    def create_playlist(
        self,
        *,
        user_id: str,
        name: str,
        description: str,
        public: bool = True,
    ) -> Mapping[str, Any]:
        return self._request_json(
            "POST",
            f"/users/{user_id}/playlists",
            {
                "name": name,
                "description": description,
                "public": public,
            },
        )

    def update_playlist_details(
        self,
        playlist_id: str,
        *,
        name: str,
        description: str,
        public: bool = True,
    ) -> None:
        self._request_json(
            "PUT",
            f"/playlists/{playlist_id}",
            {
                "name": name,
                "description": description,
                "public": public,
            },
        )

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]:
        return self._get_json(f"/playlists/{playlist_id}")

    def replace_playlist_tracks(
        self,
        playlist_id: str,
        track_uris: list[str],
    ) -> None:
        if not track_uris:
            raise SpotifyAPIError("cannot replace playlist tracks with an empty list")

        for index in range(0, len(track_uris), 100):
            batch = track_uris[index : index + 100]
            if index == 0:
                self._request_json(
                    "PUT",
                    f"/playlists/{playlist_id}/tracks",
                    {"uris": batch},
                )
            else:
                self._request_json(
                    "POST",
                    f"/playlists/{playlist_id}/tracks",
                    {"uris": batch},
                )

    def get_artist_top_tracks(
        self,
        artist_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, str] = {}
        if market:
            params["market"] = market
        query = urlencode(params)
        path = f"/artists/{artist_id}/top-tracks"
        if query:
            path = f"{path}?{query}"
        response = self._get_json(path)
        items = response.get("tracks")
        return items if isinstance(items, list) else []

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        market: str | None = None,
        limit: int = 30,
    ) -> list[Mapping[str, Any]]:
        albums: list[Mapping[str, Any]] = []
        remaining = max(limit, 0)
        offset = 0

        while remaining > 0:
            page_limit = min(50, remaining)
            params: dict[str, str | int] = {
                "include_groups": "album,single",
                "limit": page_limit,
                "offset": offset,
            }
            if market:
                params["market"] = market

            response = self._get_json(
                f"/artists/{artist_id}/albums?{urlencode(params)}"
            )
            items = response.get("items", [])
            if isinstance(items, list):
                albums.extend(item for item in items if isinstance(item, Mapping))

            if not response.get("next") or not items or len(items) < page_limit:
                break

            remaining -= len(items)
            offset += len(items)

        return albums[:limit]

    def get_tracks(
        self,
        track_ids: list[str],
    ) -> Mapping[str, Mapping[str, Any]]:
        unique_ids = [track_id for track_id in dict.fromkeys(track_ids) if track_id]
        tracks: dict[str, Mapping[str, Any]] = {}

        for index in range(0, len(unique_ids), 50):
            batch = unique_ids[index : index + 50]
            if not batch:
                continue
            response = self._get_json(f"/tracks?{urlencode({'ids': ','.join(batch)})}")
            for track in response.get("tracks", []):
                if isinstance(track, Mapping) and track.get("id"):
                    tracks[str(track["id"])] = track

        return tracks

    def upload_playlist_cover(
        self,
        playlist_id: str,
        jpeg_bytes: bytes,
    ) -> None:
        """Upload a JPEG playlist cover (requires ugc-image-upload scope)."""

        self.require_user_auth()
        encoded = base64.b64encode(jpeg_bytes)
        if len(encoded) > 256 * 1024:
            raise SpotifyAPIError(
                "playlist cover JPEG exceeds Spotify 256KB base64 payload limit"
            )
        token = self._get_access_token()
        request = Request(
            f"{self._api_base_url}/playlists/{playlist_id}/images",
            data=encoded,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "image/jpeg",
            },
            method="PUT",
        )
        try:
            self._request_with_retries(request, retryable=True)
        except SpotifyAPIError as exc:
            if "Spotify HTTP 401" not in str(exc):
                raise
            self._access_token = None
            token = self._get_access_token()
            retry_request = Request(
                f"{self._api_base_url}/playlists/{playlist_id}/images",
                data=encoded,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "image/jpeg",
                },
                method="PUT",
            )
            self._request_with_retries(retry_request, retryable=True)

    def get_album_tracks(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        tracks: list[Mapping[str, Any]] = []
        path = f"/albums/{album_id}/tracks?{urlencode({'limit': 50, **({'market': market} if market else {})})}"

        while path:
            if path.startswith("http"):
                response = self._get_json_from_url(path)
            else:
                response = self._get_json(path)

            items = response.get("items", [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, Mapping):
                        tracks.append(item)

            next_url = response.get("next")
            path = next_url if isinstance(next_url, str) and next_url else ""

        return tracks

    def get_album_track_uris(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[str]:
        uris: list[str] = []
        for item in self.get_album_tracks(album_id, market=market):
            uri = item.get("uri")
            if isinstance(uri, str) and uri:
                uris.append(uri)
        return uris

    def _get_json_from_url(self, url: str) -> Mapping[str, Any]:
        token = self._get_access_token()
        request = Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        return self._request_with_retries(request, retryable=True)

    def _get_json(self, path: str) -> Mapping[str, Any]:
        return self._request_json("GET", path)

    def _request_json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        token = self._get_access_token()
        data = None
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self._api_base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            return self._request_with_retries(request, retryable=(method.upper() == "GET"))
        except SpotifyAPIError as exc:
            if "Spotify HTTP 401" not in str(exc):
                raise

            self._access_token = None
            token = self._get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            retry_request = Request(
                f"{self._api_base_url}{path}",
                data=data,
                headers=headers,
                method=method,
            )
            return self._request_with_retries(
                retry_request,
                retryable=(method.upper() == "GET"),
            )

    def require_user_auth(self) -> None:
        if not self._credentials.refresh_token:
            raise SpotifyAPIError(
                "A Spotify user refresh token is required to create or update playlists "
                "(SPOTIFY_PUBLISH_1_REFRESH_TOKEN / SPOTIFY_PUBLISH_2_REFRESH_TOKEN)"
            )

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        if self._credentials.refresh_token:
            data = urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._credentials.refresh_token,
                }
            ).encode("utf-8")
        else:
            data = urlencode({"grant_type": "client_credentials"}).encode("utf-8")

        auth = base64.b64encode(
            f"{self._credentials.client_id}:{self._credentials.client_secret}".encode(
                "utf-8"
            )
        ).decode("ascii")
        request = Request(
            f"{self._accounts_base_url}/api/token",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        response = self._open_json(request)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise SpotifyAPIError("Spotify token response did not include access_token")

        self._access_token = token
        return token

    def _request_with_retries(
        self,
        request: Request,
        *,
        retryable: bool,
    ) -> Mapping[str, Any]:
        attempts = SPOTIFY_MAX_GET_RETRIES if retryable else 0
        for attempt in range(attempts + 1):
            try:
                return self._open_json(request)
            except SpotifyAPIError as exc:
                if not retryable or not self._should_retry(exc) or attempt >= attempts:
                    raise
                time.sleep(self._retry_delay_seconds(exc, attempt))
        raise AssertionError("unreachable")

    @staticmethod
    def _should_retry(exc: SpotifyAPIError) -> bool:
        return exc.status_code in SPOTIFY_RETRYABLE_STATUS_CODES or exc.status_code is None

    @staticmethod
    def _retry_delay_seconds(exc: SpotifyAPIError, attempt: int) -> float:
        if exc.retry_after is not None and exc.retry_after > 0:
            return exc.retry_after
        return min(2**attempt, 8)

    @staticmethod
    def _open_json(request: Request) -> Mapping[str, Any]:
        try:
            with urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SpotifyAPIError(
                f"Spotify HTTP {exc.code}: {body}",
                status_code=exc.code,
                retry_after=_retry_after_seconds(exc.headers),
            ) from exc
        except OSError as exc:
            raise SpotifyAPIError(f"Spotify request failed: {exc}") from exc

        if not payload.strip():
            return {}

        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise SpotifyAPIError("Spotify response was not a JSON object")
        return decoded


def _parse_release_date(value: str) -> date:
    if len(value) == 4:
        return date(int(value), 1, 1)
    if len(value) == 7:
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    return date.fromisoformat(value)


def _classify_album_kind(album: Mapping[str, Any], ep_min_track_count: int) -> str:
    album_type = str(album.get("album_type", "")).lower()
    album_name = str(album.get("name", "")).lower()
    total_tracks = int(album.get("total_tracks", 0))

    if album_type == "album":
        return "album"
    if album_type == "single" and (
        total_tracks >= ep_min_track_count or " ep" in f" {album_name} "
    ):
        return "ep"
    return album_type or "unknown"


def _retry_after_seconds(headers: Any) -> float | None:
    if not headers:
        return None
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None
