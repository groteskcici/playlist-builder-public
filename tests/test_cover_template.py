"""Tests for dual-beam playlist cover template."""

from __future__ import annotations

import base64
import io
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from playlist_builder.cover_template import (
    default_exo_bold_path,
    download_artist_image_bytes,
    pick_contrast_beam_color,
    relative_luminance,
    render_artist_beam_cover,
    resolve_artist_image_bytes,
    text_color_for_beam,
    _load_font,
    _wrap_title_lines,
)
from playlist_builder.playlist_cover import SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES


def _solid_image(rgb: tuple[int, int, int], size: int = 64) -> Image.Image:
    return Image.new("RGB", (size, size), rgb)


class ContrastBeamColorTests(unittest.TestCase):
    def test_vivid_image_returns_non_white_non_black(self) -> None:
        color = pick_contrast_beam_color(_solid_image((220, 90, 30)))
        self.assertTrue(all(0 <= channel <= 255 for channel in color))
        self.assertFalse(all(channel > 240 for channel in color))
        self.assertFalse(all(channel < 25 for channel in color))

    def test_prefers_backdrop_over_skin_center(self) -> None:
        # Lime frame + skin-toned center — beam should stay green-family but muted.
        image = Image.new("RGB", (64, 64), (60, 200, 70))
        for y in range(18, 46):
            for x in range(18, 46):
                image.putpixel((x, y), (210, 160, 120))
        color = pick_contrast_beam_color(image)
        self.assertGreater(color[1], color[0])
        self.assertGreater(color[1], color[2])
        self.assertLess(relative_luminance(color), relative_luminance((60, 200, 70)))
        # Not a harsh neon — editorial mute keeps channels away from 0/255 extremes.
        self.assertTrue(all(25 <= channel <= 200 for channel in color))

    def test_text_color_switches_on_luminance(self) -> None:
        self.assertEqual(text_color_for_beam((240, 220, 180)), (0, 0, 0))
        self.assertEqual(text_color_for_beam((30, 40, 60)), (255, 255, 255))
        self.assertGreater(relative_luminance((255, 255, 255)), relative_luminance((0, 0, 0)))


class CoverRenderTests(unittest.TestCase):
    def test_render_produces_jpeg_within_spotify_limit(self) -> None:
        font = default_exo_bold_path()
        self.assertTrue(font.is_file(), f"missing font: {font}")
        jpeg = render_artist_beam_cover(
            artist_image=_solid_image((40, 80, 160), size=320),
            title="VIVA LA LISA",
            size=640,
            font_path=font,
        )
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(base64.b64encode(jpeg)), SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES)
        opened = Image.open(io.BytesIO(jpeg))
        self.assertEqual(opened.size, (640, 640))
        self.assertEqual(opened.format, "JPEG")

    def test_render_requires_title(self) -> None:
        with self.assertRaises(ValueError):
            render_artist_beam_cover(
                artist_image=_solid_image((10, 10, 10)),
                title="   ",
            )

    def test_render_long_title_two_lines(self) -> None:
        font = default_exo_bold_path()
        jpeg = render_artist_beam_cover(
            artist_image=_solid_image((40, 80, 160), size=320),
            title="America 20th Anniversary World Tour Setlist",
            size=640,
            font_path=font,
        )
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(base64.b64encode(jpeg)), SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES)


class TitleWrapTests(unittest.TestCase):
    def test_wrap_splits_long_title_into_two_lines(self) -> None:
        font_path = default_exo_bold_path()
        image = Image.new("RGB", (100, 100), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = _load_font(font_path, 40)
        lines = _wrap_title_lines(
            draw,
            "AMERICA 20TH ANNIVERSARY WORLD TOUR",
            font=font,
            max_width=280,
        )
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.strip() for line in lines))


class ArtistImageFetchTests(unittest.TestCase):
    def test_download_artist_image_bytes_uses_largest_url(self) -> None:
        artist = {
            "images": [
                {"url": "https://example.com/small.jpg", "height": 160, "width": 160},
                {"url": "https://example.com/large.jpg", "height": 640, "width": 640},
            ]
        }
        seen: list[str] = []

        def fake_download(url: str) -> bytes:
            seen.append(url)
            return b"jpeg-bytes"

        payload = download_artist_image_bytes(artist, download=fake_download)
        self.assertEqual(payload, b"jpeg-bytes")
        self.assertEqual(seen, ["https://example.com/large.jpg"])

    def test_resolve_artist_image_bytes_via_client(self) -> None:
        class FakeClient:
            def get_artists(self, artist_ids: list[str]):
                return {
                    artist_ids[0]: {
                        "id": artist_ids[0],
                        "images": [{"url": "https://cdn.example/a.jpg", "height": 640}],
                    }
                }

        payload = resolve_artist_image_bytes(
            FakeClient(),
            "artist123",
            download=lambda url: b"ok",
        )
        self.assertEqual(payload, b"ok")


if __name__ == "__main__":
    unittest.main()
