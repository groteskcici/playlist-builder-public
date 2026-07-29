"""Tests for album playlist cover resolution."""

from __future__ import annotations

import base64
import unittest

from playlist_builder.playlist_cover import (
    SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES,
    cover_image_candidates,
    jpeg_within_playlist_cover_limit,
    pick_playlist_cover_jpeg,
    playlist_cover_jpeg_from_track_uris,
    track_id_from_uri,
)


class PlaylistCoverTests(unittest.TestCase):
    def test_track_id_from_uri(self) -> None:
        self.assertEqual(track_id_from_uri("spotify:track:abc123"), "abc123")
        self.assertIsNone(track_id_from_uri("spotify:album:abc123"))

    def test_cover_image_candidates_largest_first(self) -> None:
        urls = cover_image_candidates(
            [
                {"height": 64, "url": "https://img/64.jpg"},
                {"height": 640, "url": "https://img/640.jpg"},
                {"height": 300, "url": "https://img/300.jpg"},
            ]
        )
        self.assertEqual(
            urls,
            [
                "https://img/640.jpg",
                "https://img/300.jpg",
                "https://img/64.jpg",
            ],
        )

    def test_pick_skips_oversized_then_uses_smaller(self) -> None:
        too_big = b"x" * (SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES)
        ok = b"jpeg-ok"

        def download(url: str) -> bytes:
            if url.endswith("640.jpg"):
                return too_big
            return ok

        picked = pick_playlist_cover_jpeg(
            image_urls=["https://img/640.jpg", "https://img/300.jpg"],
            download=download,
        )
        self.assertEqual(picked, ok)
        self.assertTrue(jpeg_within_playlist_cover_limit(ok))

    def test_playlist_cover_from_first_track(self) -> None:
        def get_tracks(track_ids: list[str]):
            return {
                "t1": {
                    "id": "t1",
                    "album": {
                        "images": [
                            {"height": 300, "url": "https://img/t1.jpg"},
                        ]
                    },
                }
            }

        cover = playlist_cover_jpeg_from_track_uris(
            ["spotify:track:t1", "spotify:track:t2"],
            get_tracks=get_tracks,
            download=lambda url: b"cover-bytes",
        )
        self.assertEqual(cover, b"cover-bytes")
        self.assertLessEqual(len(base64.b64encode(cover)), SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES)


if __name__ == "__main__":
    unittest.main()
