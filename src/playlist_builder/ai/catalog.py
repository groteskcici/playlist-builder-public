"""Build artist catalog pools for album playlist AI jobs."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol

from playlist_builder.ai.schemas import CatalogTrack


class ArtistCatalogClient(Protocol):
    def get_artist_top_tracks(
        self,
        artist_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        market: str | None = None,
        limit: int = 30,
    ) -> list[Mapping[str, Any]]: ...

    def get_album_track_uris(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[str]: ...

    def get_tracks(
        self,
        track_ids: list[str],
    ) -> Mapping[str, Mapping[str, Any]]: ...


def build_artist_catalog_pool(
    client: ArtistCatalogClient,
    *,
    artist_id: str,
    project_title: str,
    exclude_uris: set[str],
    market: str | None = None,
    max_pool_size: int = 80,
) -> list[CatalogTrack]:
    pool: dict[str, CatalogTrack] = {}

    for track in client.get_artist_top_tracks(artist_id, market=market):
        catalog_track = _catalog_track_from_spotify_track(track)
        if catalog_track and _should_include(catalog_track, project_title, exclude_uris):
            pool[catalog_track.uri] = catalog_track

    track_ids_needing_popularity: list[str] = []
    for album in client.get_artist_albums(artist_id, market=market):
        album_id = album.get("id")
        album_name = str(album.get("name", "")).strip()
        album_type = str(album.get("album_type", "album")).strip() or "album"
        if not album_id or _title_similarity(album_name, project_title) >= 0.82:
            continue

        for uri in client.get_album_track_uris(str(album_id), market=market):
            if uri in exclude_uris or uri in pool:
                continue
            track_id = _track_id_from_uri(uri)
            track_ids_needing_popularity.append(track_id)

    for index in range(0, len(track_ids_needing_popularity), 50):
        batch_ids = track_ids_needing_popularity[index : index + 50]
        tracks_by_id = client.get_tracks(batch_ids)
        for track_id, track in tracks_by_id.items():
            catalog_track = _catalog_track_from_spotify_track(track)
            if catalog_track and _should_include(catalog_track, project_title, exclude_uris):
                pool[catalog_track.uri] = catalog_track
            if len(pool) >= max_pool_size:
                break
        if len(pool) >= max_pool_size:
            break

    ranked = sorted(
        pool.values(),
        key=lambda track: track.popularity if track.popularity is not None else -1,
        reverse=True,
    )
    return ranked[:max_pool_size]


def _should_include(
    track: CatalogTrack,
    project_title: str,
    exclude_uris: set[str],
) -> bool:
    if track.uri in exclude_uris:
        return False
    if _title_similarity(track.album_name, project_title) >= 0.82:
        return False
    return bool(track.uri.startswith("spotify:track:"))


def _catalog_track_from_spotify_track(track: Mapping[str, Any]) -> CatalogTrack | None:
    uri = track.get("uri")
    name = track.get("name")
    if not isinstance(uri, str) or not uri or not isinstance(name, str) or not name.strip():
        return None

    album = track.get("album")
    album_name = ""
    album_type = "unknown"
    if isinstance(album, Mapping):
        album_name = str(album.get("name", "")).strip()
        album_type = str(album.get("album_type", "unknown")).strip() or "unknown"

    popularity = track.get("popularity")
    return CatalogTrack(
        uri=uri,
        name=name.strip(),
        album_name=album_name,
        album_type=album_type,
        popularity=popularity if isinstance(popularity, int) else None,
    )


def _track_id_from_uri(uri: str) -> str:
    return uri.rsplit(":", maxsplit=1)[-1]


def _title_similarity(left: str, right: str) -> float:
    left_norm = _normalize_title(left)
    right_norm = _normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _normalize_title(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())
