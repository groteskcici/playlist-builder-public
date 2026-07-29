"""Resolve and prepare Spotify playlist cover images from track artwork."""

from __future__ import annotations

import base64
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES = 256 * 1024
_TRACK_URI_PREFIX = "spotify:track:"


GetTracksFn = Callable[[list[str]], Mapping[str, Mapping[str, Any]]]
DownloadFn = Callable[[str], bytes]


def track_id_from_uri(uri: str) -> str | None:
    text = uri.strip()
    if not text.startswith(_TRACK_URI_PREFIX):
        return None
    track_id = text.removeprefix(_TRACK_URI_PREFIX).strip()
    return track_id or None


def cover_image_candidates(images: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """Return image URLs largest-first (Spotify album art sizes)."""

    if not images:
        return []
    ranked: list[tuple[int, str]] = []
    for item in images:
        if not isinstance(item, Mapping):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        height = item.get("height")
        width = item.get("width")
        size = 0
        if isinstance(height, int) and height > 0:
            size = height
        elif isinstance(width, int) and width > 0:
            size = width
        ranked.append((size, url.strip()))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [url for _, url in ranked]


def download_image_bytes(url: str, *, timeout: float = 20.0) -> bytes:
    request = Request(url, method="GET", headers={"User-Agent": "playlist-builder/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"failed to download cover image: {exc}") from exc


def jpeg_within_playlist_cover_limit(jpeg_bytes: bytes) -> bool:
    encoded = base64.b64encode(jpeg_bytes)
    return len(encoded) <= SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES


def pick_playlist_cover_jpeg(
    *,
    image_urls: Sequence[str],
    download: DownloadFn = download_image_bytes,
) -> bytes | None:
    """Download the largest cover image that fits Spotify's playlist cover limit."""

    for url in image_urls:
        try:
            payload = download(url)
        except RuntimeError:
            continue
        if not payload:
            continue
        # Spotify requires JPEG; CDN album art is almost always JPEG already.
        if not jpeg_within_playlist_cover_limit(payload):
            continue
        return payload
    return None


def playlist_cover_jpeg_from_track_uris(
    track_uris: Sequence[str],
    *,
    get_tracks: GetTracksFn,
    download: DownloadFn = download_image_bytes,
) -> bytes | None:
    """Use album artwork from the first resolvable track URI as playlist cover."""

    track_ids: list[str] = []
    for uri in track_uris:
        track_id = track_id_from_uri(str(uri))
        if track_id:
            track_ids.append(track_id)
    if not track_ids:
        return None

    tracks = get_tracks(track_ids)
    for track_id in track_ids:
        track = tracks.get(track_id)
        if not isinstance(track, Mapping):
            continue
        album = track.get("album")
        if not isinstance(album, Mapping):
            continue
        images = album.get("images")
        if not isinstance(images, list):
            continue
        urls = cover_image_candidates(images)
        cover = pick_playlist_cover_jpeg(image_urls=urls, download=download)
        if cover is not None:
            return cover
    return None
