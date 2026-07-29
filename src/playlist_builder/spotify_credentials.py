"""Spotify credential loading — API account vs publish accounts."""

from __future__ import annotations

import os
from dataclasses import dataclass

from playlist_builder.spotify_album_watcher import (
    SpotifyCredentials,
    _load_env_files,
)


@dataclass(frozen=True, slots=True)
class SpotifyPublishSlot:
    slot: str
    client_id: str
    client_secret: str
    refresh_token: str


def api_refresh_token_from_env() -> str | None:
    _load_env_files()
    token = os.environ.get("SPOTIFY_API_REFRESH_TOKEN", "").strip()
    if token:
        return token
    legacy = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
    return legacy or None


def _global_app_credentials_from_env() -> tuple[str, str]:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")
    return client_id, client_secret


def _publish_slot_from_env(index: int) -> SpotifyPublishSlot | None:
    refresh_token = os.environ.get(f"SPOTIFY_PUBLISH_{index}_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        return None

    client_id = os.environ.get(f"SPOTIFY_PUBLISH_{index}_CLIENT_ID", "").strip()
    client_secret = os.environ.get(f"SPOTIFY_PUBLISH_{index}_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        client_id, client_secret = _global_app_credentials_from_env()

    return SpotifyPublishSlot(
        slot=str(index),
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )


def publish_slots_from_env() -> tuple[SpotifyPublishSlot, ...]:
    """Return configured publish slots (1, 2, …). Falls back to API/legacy token."""
    _load_env_files()
    slots: list[SpotifyPublishSlot] = []
    for index in (1, 2):
        slot = _publish_slot_from_env(index)
        if slot is not None:
            slots.append(slot)

    if slots:
        return tuple(slots)

    fallback = api_refresh_token_from_env()
    if fallback:
        client_id, client_secret = _global_app_credentials_from_env()
        return (
            SpotifyPublishSlot(
                slot="1",
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=fallback,
            ),
        )
    return ()


def api_credentials_from_env() -> SpotifyCredentials:
    _load_env_files()
    client_id, client_secret = _global_app_credentials_from_env()

    return SpotifyCredentials(
        client_id=client_id,
        client_secret=client_secret,
        access_token=os.environ.get("SPOTIFY_ACCESS_TOKEN"),
        refresh_token=api_refresh_token_from_env(),
    )


def publish_credentials_for_slot(slot: SpotifyPublishSlot | str) -> SpotifyCredentials:
    _load_env_files()
    if isinstance(slot, str):
        configs = {item.slot: item for item in publish_slots_from_env()}
        config = configs.get(slot)
        if config is None and configs:
            config = next(iter(configs.values()))
        if config is None:
            raise RuntimeError(
                "No Spotify publish refresh token configured "
                "(set SPOTIFY_PUBLISH_1_REFRESH_TOKEN / SPOTIFY_PUBLISH_2_REFRESH_TOKEN)"
            )
        slot = config

    return SpotifyCredentials(
        client_id=slot.client_id,
        client_secret=slot.client_secret,
        refresh_token=slot.refresh_token,
    )
