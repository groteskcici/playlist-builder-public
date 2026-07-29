"""Environment loading helpers for persistence."""

from __future__ import annotations

import os
from pathlib import Path

from playlist_builder.spotify_album_watcher import load_spotify_env_file


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_env_files() -> None:
    root = project_root()
    seen: set[Path] = set()
    for candidate in (Path(".env"), root / ".env"):
        env_path = candidate.resolve()
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        load_spotify_env_file(env_path)


def database_url_from_env() -> str:
    load_env_files()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set")
    return database_url
