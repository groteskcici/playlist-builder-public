"""Initialize persistence schema (SQLite or Postgres from DATABASE_URL)."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.persistence import store_from_env  # noqa: E402


def main() -> int:
    store = store_from_env()
    store.init_schema()

    print("Database schema initialized (album + research candidate tables).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
