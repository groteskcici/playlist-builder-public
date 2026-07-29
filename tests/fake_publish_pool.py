"""Test double for SpotifyPublishPool."""

from __future__ import annotations

from typing import Any, Protocol

from playlist_builder.spotify_publish_pool import PlaylistWritePlan, PlaylistWriteResult


class _FakePlaylistClient(Protocol):
    def require_user_auth(self) -> None: ...

    def get_current_user(self) -> dict[str, Any]: ...

    def create_playlist(
        self,
        *,
        user_id: str,
        name: str,
        description: str,
        public: bool = True,
    ) -> dict[str, Any]: ...

    def update_playlist_details(
        self,
        playlist_id: str,
        *,
        name: str,
        description: str,
        public: bool = True,
    ) -> None: ...

    def replace_playlist_tracks(self, playlist_id: str, track_uris: list[str]) -> None: ...

    def upload_playlist_cover(self, playlist_id: str, jpeg_bytes: bytes) -> None: ...


class FakePublishPool:
    def __init__(self, client: _FakePlaylistClient) -> None:
        self.client = client
        self._next_slot = "1"
        self.last_cover_jpeg: bytes | None = None

    def write_playlist(
        self,
        *,
        plan: PlaylistWritePlan,
        track_uris: list[str],
        existing_playlist_id: str | None,
        existing_slot: str | None,
        cover_jpeg: bytes | None = None,
    ) -> PlaylistWriteResult:
        self.last_cover_jpeg = cover_jpeg
        if existing_playlist_id:
            self.client.update_playlist_details(
                existing_playlist_id,
                name=plan.name,
                description=plan.description,
                public=plan.public,
            )
            self.client.replace_playlist_tracks(existing_playlist_id, track_uris)
            if cover_jpeg:
                self.client.upload_playlist_cover(existing_playlist_id, cover_jpeg)
            return PlaylistWriteResult(
                playlist_id=existing_playlist_id,
                playlist_uri=f"spotify:playlist:{existing_playlist_id}",
                action="updated",
                publish_slot=existing_slot or "1",
            )

        slot = self._next_slot
        self._next_slot = "2" if slot == "1" else "1"
        user = self.client.get_current_user()
        created = self.client.create_playlist(
            user_id=str(user["id"]),
            name=plan.name,
            description=plan.description,
            public=plan.public,
        )
        playlist_id = str(created["id"])
        playlist_uri = str(created.get("uri") or f"spotify:playlist:{playlist_id}")
        self.client.replace_playlist_tracks(playlist_id, track_uris)
        if cover_jpeg:
            self.client.upload_playlist_cover(playlist_id, cover_jpeg)
        return PlaylistWriteResult(
            playlist_id=playlist_id,
            playlist_uri=playlist_uri,
            action="created",
            publish_slot=slot,
        )
