"""Round-robin Spotify publish clients for multiple playlist owner accounts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from playlist_builder.spotify_album_watcher import SpotifyAPIError, SpotifyWebAPIClient
from playlist_builder.spotify_credentials import (
    SpotifyPublishSlot,
    publish_credentials_for_slot,
    publish_slots_from_env,
)


class PlaylistWritePlan(Protocol):
    name: str
    description: str
    public: bool


@dataclass(frozen=True, slots=True)
class PlaylistWriteResult:
    playlist_id: str
    playlist_uri: str
    action: str
    publish_slot: str


class SpotifyPublishPool:
    def __init__(
        self,
        slots: tuple[SpotifyPublishSlot, ...],
        *,
        state_path: Path,
    ) -> None:
        if not slots:
            raise RuntimeError(
                "No Spotify publish accounts configured. "
                "Set SPOTIFY_PUBLISH_1_REFRESH_TOKEN and SPOTIFY_PUBLISH_2_REFRESH_TOKEN."
            )
        self._slots = slots
        self._state_path = state_path
        self._clients: dict[str, SpotifyWebAPIClient] = {}

    @classmethod
    def from_env(cls, *, state_path: Path | None = None) -> "SpotifyPublishPool":
        slots = publish_slots_from_env()
        if state_path is None:
            from playlist_builder.spotify_album_watcher import _load_env_files
            import os

            _load_env_files()
            raw = os.environ.get("SPOTIFY_PUBLISH_STATE_FILE", "").strip()
            if raw:
                state_path = Path(raw)
            else:
                project_root = Path(__file__).resolve().parents[2]
                state_path = project_root / "data" / "spotify_publish_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(slots, state_path=state_path)

    @property
    def slot_ids(self) -> tuple[str, ...]:
        return tuple(item.slot for item in self._slots)

    def client_for_slot(self, slot: str | None) -> tuple[str, SpotifyWebAPIClient]:
        if slot and slot in self.slot_ids:
            active_slot = slot
        else:
            active_slot = self._slots[0].slot
        return active_slot, self._get_client(active_slot)

    def client_for_new_playlist(self) -> tuple[str, SpotifyWebAPIClient]:
        index = self._read_next_index()
        slot = self._slots[index % len(self._slots)].slot
        self._write_next_index((index + 1) % len(self._slots))
        return slot, self._get_client(slot)

    def write_playlist(
        self,
        *,
        plan: PlaylistWritePlan,
        track_uris: list[str],
        existing_playlist_id: str | None,
        existing_slot: str | None,
        cover_jpeg: bytes | None = None,
    ) -> PlaylistWriteResult:
        if existing_playlist_id:
            publish_slot, client = self.client_for_slot(existing_slot)
            action = "updated"
            playlist_id = existing_playlist_id
            playlist_uri = None
        else:
            publish_slot, client = self.client_for_new_playlist()
            action = "created"
            playlist_id = None
            playlist_uri = None

        client.require_user_auth()
        if playlist_id:
            client.update_playlist_details(
                playlist_id,
                name=plan.name,
                description=plan.description,
                public=plan.public,
            )
            client.replace_playlist_tracks(playlist_id, track_uris)
            if playlist_uri is None:
                playlist_uri = f"spotify:playlist:{playlist_id}"
        else:
            user = client.get_current_user()
            user_id = user.get("id")
            if not isinstance(user_id, str) or not user_id:
                raise RuntimeError("Spotify /me response did not include user id")
            created = client.create_playlist(
                user_id=user_id,
                name=plan.name,
                description=plan.description,
                public=plan.public,
            )
            playlist_id = str(created.get("id", "")).strip()
            playlist_uri = str(created.get("uri", "")).strip()
            if not playlist_id:
                raise RuntimeError("Spotify create playlist response did not include id")
            client.replace_playlist_tracks(playlist_id, track_uris)

        if cover_jpeg:
            try:
                client.upload_playlist_cover(playlist_id, cover_jpeg)
            except SpotifyAPIError:
                # Missing ugc-image-upload scope or transient image API errors
                # should not block playlist create/update.
                pass

        return PlaylistWriteResult(
            playlist_id=playlist_id,
            playlist_uri=playlist_uri or f"spotify:playlist:{playlist_id}",
            action=action,
            publish_slot=publish_slot,
        )

    def _get_client(self, slot: str) -> SpotifyWebAPIClient:
        if slot not in self._clients:
            config = next(item for item in self._slots if item.slot == slot)
            self._clients[slot] = SpotifyWebAPIClient(
                publish_credentials_for_slot(config)
            )
        return self._clients[slot]

    def _read_next_index(self) -> int:
        if not self._state_path.is_file():
            return 0
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        value = payload.get("next_index") if isinstance(payload, Mapping) else None
        if isinstance(value, int) and value >= 0:
            return value % len(self._slots)
        return 0

    def _write_next_index(self, index: int) -> None:
        payload = {"next_index": index}
        self._state_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
