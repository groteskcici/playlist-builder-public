"""Render a two-beam playlist cover from a local image or Spotify artist id."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.cover_template import (  # noqa: E402
    default_exo_bold_path,
    render_artist_beam_cover,
    render_cover_from_artist_id,
)
from playlist_builder.persistence.env import load_env_files  # noqa: E402
from playlist_builder.spotify_album_watcher import (  # noqa: E402
    SpotifyCredentials,
    SpotifyWebAPIClient,
)
from playlist_builder.spotify_credentials import (  # noqa: E402
    _global_app_credentials_from_env,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a white-border dual-beam playlist cover JPEG."
    )
    parser.add_argument("--title", required=True, help="Playlist title drawn on the small beam")
    parser.add_argument("--out", required=True, help="Output JPEG path")
    parser.add_argument("--image-path", help="Local artist image path")
    parser.add_argument("--artist-id", help="Spotify artist id (uses /artists images)")
    parser.add_argument("--size", type=int, default=640, help="Square canvas size (default 640)")
    parser.add_argument(
        "--font-path",
        default=None,
        help="Override Exo Bold path (default ops/Exo/static/Exo-Bold.ttf)",
    )
    args = parser.parse_args()

    if bool(args.image_path) == bool(args.artist_id):
        parser.error("provide exactly one of --image-path or --artist-id")

    font_path = Path(args.font_path) if args.font_path else default_exo_bold_path()
    if args.image_path:
        image_bytes = Path(args.image_path).read_bytes()
        jpeg = render_artist_beam_cover(
            artist_image=image_bytes,
            title=args.title,
            size=args.size,
            font_path=font_path,
        )
    else:
        load_env_files()
        # Artist images are public metadata — use client_credentials only.
        # Do not attach SPOTIFY_*_REFRESH_TOKEN (user tokens can be revoked).
        client_id, client_secret = _global_app_credentials_from_env()
        client = SpotifyWebAPIClient(
            SpotifyCredentials(client_id=client_id, client_secret=client_secret)
        )
        jpeg = render_cover_from_artist_id(
            client,
            artist_id=str(args.artist_id).strip(),
            title=args.title,
            size=args.size,
            font_path=font_path,
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(jpeg)
    print(out_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
