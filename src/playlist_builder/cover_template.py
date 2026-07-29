"""Compose two-beam playlist covers from Spotify artist images."""

from __future__ import annotations

import colorsys
import io
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from PIL import Image, ImageDraw, ImageFont

from playlist_builder.persistence.env import project_root
from playlist_builder.playlist_cover import (
    SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES,
    cover_image_candidates,
    download_image_bytes,
    jpeg_within_playlist_cover_limit,
)

DEFAULT_COVER_SIZE = 640
DEFAULT_BORDER_RATIO = 0.08
DEFAULT_LARGE_BEAM_RATIO = 0.30
DEFAULT_SMALL_BEAM_RATIO = 0.24
EXO_BOLD_RELATIVE = Path("ops") / "Exo" / "static" / "Exo-Bold.ttf"

Rgb = tuple[int, int, int]
DownloadFn = Callable[[str], bytes]


class ArtistImageClient(Protocol):
    def get_artists(self, artist_ids: list[str]) -> Mapping[str, Mapping[str, Any]]: ...


def default_exo_bold_path(*, root: Path | None = None) -> Path:
    return (root or project_root()) / EXO_BOLD_RELATIVE


def relative_luminance(rgb: Rgb) -> float:
    r, g, b = (channel / 255.0 for channel in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_color_for_beam(beam_rgb: Rgb) -> Rgb:
    return (0, 0, 0) if relative_luminance(beam_rgb) >= 0.55 else (255, 255, 255)


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _quantize_rgb(rgb: Rgb, *, step: int = 24) -> Rgb:
    return (
        min(255, (rgb[0] // step) * step),
        min(255, (rgb[1] // step) * step),
        min(255, (rgb[2] // step) * step),
    )


def _is_skin_tone_hsv(h: float, s: float, v: float) -> bool:
    """Rough skin/hair band to avoid face-dominated beams on portraits."""

    if s < 0.12 or v < 0.18:
        return False
    # Orange–yellow skin-ish hues.
    return 0.02 <= h <= 0.13 and s <= 0.65


def _border_weighted_pixels(sample: Image.Image) -> list[Rgb]:
    """Collect pixels with heavy weight on the outer frame (backdrop colors)."""

    width, height = sample.size
    border = max(2, min(width, height) // 5)
    weighted: list[Rgb] = []
    for y in range(height):
        for x in range(width):
            pixel = sample.getpixel((x, y))
            if not isinstance(pixel, tuple) or len(pixel) < 3:
                continue
            rgb = (int(pixel[0]), int(pixel[1]), int(pixel[2]))
            on_border = x < border or y < border or x >= width - border or y >= height - border
            # Backdrop usually lives on the frame; weight it 4x vs face center.
            repeats = 4 if on_border else 1
            weighted.extend([rgb] * repeats)
    return weighted


def _editorial_beam_from_backdrop(h: float, s: float, v: float) -> Rgb:
    """Turn a backdrop hue into a muted, Spotify-ish graphic bar color.

    Editorial covers avoid neon/raw HSV. They look dusty/pastel: lower
    saturation, softened value, slight warm-neutral mix.
    """

    # Pull saturation down into a "print" range.
    mute_s = max(0.28, min(0.62, s * 0.55 + 0.12))
    # Soft mid-dark — not crushed black, not neon bright.
    if v >= 0.65:
        mute_v = 0.46
    elif v >= 0.40:
        mute_v = 0.40
    else:
        mute_v = 0.36

    rr, gg, bb = colorsys.hsv_to_rgb(h, mute_s, mute_v)
    # Blend a touch of warm gray so it feels printed, not digital-pure.
    warm_gray = (0.42, 0.40, 0.38)
    mix = 0.18
    rr = rr * (1.0 - mix) + warm_gray[0] * mix
    gg = gg * (1.0 - mix) + warm_gray[1] * mix
    bb = bb * (1.0 - mix) + warm_gray[2] * mix
    return (_clamp_byte(rr * 255), _clamp_byte(gg * 255), _clamp_byte(bb * 255))


def pick_contrast_beam_color(image: Image.Image) -> Rgb:
    """Pick a muted same-family beam color from the photo backdrop."""

    sample = image.convert("RGB").resize((64, 64), Image.Resampling.BOX)
    pixels = _border_weighted_pixels(sample)
    if not pixels:
        return _editorial_beam_from_backdrop(0.33, 0.55, 0.70)

    counts: Counter[Rgb] = Counter(_quantize_rgb(pixel) for pixel in pixels)
    backdrop: Rgb | None = None
    best_score = float("-inf")
    for color, count in counts.most_common(40):
        r, g, b = (channel / 255.0 for channel in color)
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if v < 0.12 or v > 0.97:
            continue
        if s < 0.10:
            continue
        if _is_skin_tone_hsv(h, s, v):
            continue
        frequency = count / len(pixels)
        score = (s * 2.0) + (frequency * 3.0) + (min(v, 0.9) * 0.4)
        if score > best_score:
            best_score = score
            backdrop = color

    if backdrop is None:
        mean_r = sum(p[0] for p in pixels) / len(pixels)
        mean_g = sum(p[1] for p in pixels) / len(pixels)
        mean_b = sum(p[2] for p in pixels) / len(pixels)
        h, s, v = colorsys.rgb_to_hsv(mean_r / 255.0, mean_g / 255.0, mean_b / 255.0)
    else:
        h, s, v = colorsys.rgb_to_hsv(
            backdrop[0] / 255.0,
            backdrop[1] / 255.0,
            backdrop[2] / 255.0,
        )

    return _editorial_beam_from_backdrop(h, s, v)


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except OSError:
        return ImageFont.load_default()


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_title_lines(
    draw: ImageDraw.ImageDraw,
    title: str,
    *,
    font: ImageFont.ImageFont,
    max_width: int,
) -> tuple[str, ...]:
    """Return 1 line if it fits, otherwise up to 2 word-wrapped lines."""

    text = " ".join(title.split()).strip()
    if not text:
        return ()
    width, _ = _text_size(draw, text, font)
    if width <= max_width:
        return (text,)

    words = text.split(" ")
    if len(words) == 1:
        # Hard-split a single long token near the middle.
        mid = max(1, len(text) // 2)
        return (text[:mid].rstrip("-"), text[mid:].lstrip("-") or text[:mid])

    best: tuple[str, str] | None = None
    best_score = float("inf")
    for split_at in range(1, len(words)):
        line1 = " ".join(words[:split_at])
        line2 = " ".join(words[split_at:])
        w1, _ = _text_size(draw, line1, font)
        w2, _ = _text_size(draw, line2, font)
        if w1 > max_width or w2 > max_width:
            continue
        # Prefer balanced lines that both fit.
        score = abs(w1 - w2) + abs(len(line1) - len(line2)) * 0.25
        if score < best_score:
            best_score = score
            best = (line1, line2)
    if best is not None:
        return best

    # Fallback: put as many words as fit on line 1, rest on line 2.
    line1_words: list[str] = []
    for word in words:
        candidate = " ".join([*line1_words, word])
        w, _ = _text_size(draw, candidate, font)
        if line1_words and w > max_width:
            break
        line1_words.append(word)
    if not line1_words:
        line1_words = [words[0]]
    line1 = " ".join(line1_words)
    line2 = " ".join(words[len(line1_words) :]) or line1
    return (line1, line2)


def _fit_title_layout(
    draw: ImageDraw.ImageDraw,
    title: str,
    *,
    font_path: Path,
    max_width: int,
    max_height: int,
    start_size: int,
) -> tuple[ImageFont.ImageFont, tuple[str, ...]]:
    """Shrink font until title fits in 1–2 lines inside the beam."""

    size = max(12, start_size)
    last_font = _load_font(font_path, 12)
    last_lines: tuple[str, ...] = (title,)
    while size >= 12:
        font = _load_font(font_path, size)
        lines = _wrap_title_lines(draw, title, font=font, max_width=max_width)
        if not lines:
            return font, (title,)
        widths = [_text_size(draw, line, font)[0] for line in lines]
        heights = [_text_size(draw, line, font)[1] for line in lines]
        line_gap = max(2, int(round(size * 0.12))) if len(lines) > 1 else 0
        total_h = sum(heights) + line_gap * (len(lines) - 1)
        last_font, last_lines = font, lines
        if max(widths, default=0) <= max_width and total_h <= max_height:
            return font, lines
        size -= 2
    return last_font, last_lines


def _square_cover_photo(image: Image.Image, photo_size: int) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = rgb.crop((left, top, left + side, top + side))
    return cropped.resize((photo_size, photo_size), Image.Resampling.LANCZOS)


def render_artist_beam_cover(
    *,
    artist_image: Image.Image | bytes,
    title: str,
    size: int = DEFAULT_COVER_SIZE,
    font_path: Path | None = None,
    border_ratio: float = DEFAULT_BORDER_RATIO,
    large_beam_ratio: float = DEFAULT_LARGE_BEAM_RATIO,
    small_beam_ratio: float = DEFAULT_SMALL_BEAM_RATIO,
    jpeg_quality: int = 90,
) -> bytes:
    """Render white-border + dual-beam cover JPEG bytes."""

    if size < 200:
        raise ValueError("size must be at least 200")
    text = " ".join(str(title or "").split()).strip()
    if not text:
        raise ValueError("title must be non-empty")

    if isinstance(artist_image, bytes):
        source = Image.open(io.BytesIO(artist_image))
    else:
        source = artist_image

    border = max(8, int(round(size * border_ratio)))
    photo_size = size - (border * 2)
    if photo_size < 64:
        raise ValueError("border too large for canvas size")

    beam_rgb = pick_contrast_beam_color(source.convert("RGB"))
    text_rgb = text_color_for_beam(beam_rgb)
    font_file = font_path or default_exo_bold_path()

    photo = _square_cover_photo(source, photo_size)
    photo_left = border
    photo_top = border

    small_beam_h = max(24, int(round(photo_size * small_beam_ratio)))
    large_beam_h = max(16, int(round(size * large_beam_ratio)))
    # Side-peek large beam stays taller than the front small beam.
    large_beam_h = max(large_beam_h, small_beam_h + max(8, size // 40))
    large_beam_top = (size - large_beam_h) // 2
    small_top = photo_top + (photo_size - small_beam_h) // 2

    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (0, large_beam_top, size, large_beam_top + large_beam_h),
        fill=beam_rgb,
    )
    canvas.paste(photo, (photo_left, photo_top))
    draw.rectangle(
        (0, small_top, size, small_top + small_beam_h),
        fill=beam_rgb,
    )

    padding_x = max(8, int(round(size * 0.06)))
    padding_y = max(6, int(round(small_beam_h * 0.14)))
    font, lines = _fit_title_layout(
        draw,
        text.upper(),
        font_path=font_file,
        max_width=size - (padding_x * 2),
        max_height=small_beam_h - (padding_y * 2),
        start_size=max(18, int(round(small_beam_h * 0.42))),
    )
    line_sizes = [_text_size(draw, line, font) for line in lines]
    line_gap = max(2, int(round(getattr(font, "size", 16) * 0.12))) if len(lines) > 1 else 0
    total_h = sum(height for _width, height in line_sizes) + line_gap * (len(lines) - 1)
    cursor_y = small_top + (small_beam_h - total_h) // 2
    for index, line in enumerate(lines):
        line_w, line_h = line_sizes[index]
        bbox = draw.textbbox((0, 0), line, font=font)
        text_x = (size - line_w) // 2 - bbox[0]
        text_y = cursor_y - bbox[1]
        draw.text((text_x, text_y), line, fill=text_rgb, font=font)
        cursor_y += line_h + line_gap

    quality = jpeg_quality
    payload = b""
    while quality >= 55:
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=quality, optimize=True)
        payload = buffer.getvalue()
        if jpeg_within_playlist_cover_limit(payload):
            return payload
        quality -= 5
    if not jpeg_within_playlist_cover_limit(payload):
        raise RuntimeError(
            f"cover JPEG exceeds Spotify base64 limit ({SPOTIFY_PLAYLIST_COVER_MAX_BASE64_BYTES} bytes)"
        )
    return payload


def artist_image_urls(artist: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(artist, Mapping):
        return []
    images = artist.get("images")
    if not isinstance(images, list):
        return []
    return cover_image_candidates(images)


def download_artist_image_bytes(
    artist: Mapping[str, Any],
    *,
    download: DownloadFn = download_image_bytes,
) -> bytes | None:
    for url in artist_image_urls(artist):
        try:
            payload = download(url)
        except RuntimeError:
            continue
        if payload:
            return payload
    return None


def resolve_artist_image_bytes(
    client: ArtistImageClient,
    artist_id: str,
    *,
    download: DownloadFn = download_image_bytes,
) -> bytes | None:
    """Fetch largest usable artist image bytes via Spotify /artists."""

    artists = client.get_artists([artist_id])
    artist = artists.get(artist_id)
    if artist is None:
        return None
    return download_artist_image_bytes(artist, download=download)


def render_cover_from_artist_id(
    client: ArtistImageClient,
    *,
    artist_id: str,
    title: str,
    size: int = DEFAULT_COVER_SIZE,
    font_path: Path | None = None,
    download: DownloadFn = download_image_bytes,
) -> bytes:
    image_bytes = resolve_artist_image_bytes(client, artist_id, download=download)
    if not image_bytes:
        raise RuntimeError(f"no artist image available for {artist_id}")
    return render_artist_beam_cover(
        artist_image=image_bytes,
        title=title,
        size=size,
        font_path=font_path,
    )
