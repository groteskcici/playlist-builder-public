"""SQLite persistence for local development."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from playlist_builder.persistence.models import (
    CandidateStateRow,
    PersistenceError,
    parse_candidate_state_row,
)
from playlist_builder.persistence.album_tier_cache import (
    AlbumTierCacheRow,
    normalize_artist_name,
)
from playlist_builder.persistence.validation_record import (
    ValidationPersistenceRecord,
    payload_to_json,
)

_CANDIDATE_SELECT = """
    SELECT
        dedupe_key, source, source_event_id, event_type,
        artist_name, project_title, event_date, spotify_artist_id,
        effective_status, artist_value_tier, prerelease_uri,
        resolved_track_count, resolved_track_uris, track_uri_hash,
        score_action,
        last_checked_at, last_change_at, next_check_at, publish_pending,
        spotify_playlist_id, spotify_playlist_uri, last_published_at,
        spotify_publish_slot
    FROM candidate_state
"""


class SqliteStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @classmethod
    def from_path(cls, database_path: str | Path) -> "SqliteStore":
        return cls(Path(database_path))

    @classmethod
    def from_url(cls, database_url: str) -> "SqliteStore":
        if not database_url.startswith("sqlite:///"):
            raise PersistenceError(f"unsupported SQLite URL: {database_url}")
        raw_path = database_url.removeprefix("sqlite:///")
        return cls(Path(raw_path))

    def init_schema(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema_sqlite.sql")
        with self._connection() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._ensure_column(conn, "candidate_state", "score_action", "TEXT NOT NULL DEFAULT 'ignore'")
            self._ensure_column(conn, "candidate_state", "spotify_playlist_id", "TEXT")
            self._ensure_column(conn, "candidate_state", "spotify_playlist_uri", "TEXT")
            self._ensure_column(conn, "candidate_state", "last_published_at", "TEXT")
            self._ensure_column(conn, "candidate_state", "spotify_publish_slot", "TEXT")

    def get_candidate_state(self, dedupe_key: str) -> CandidateStateRow | None:
        with self._connection() as conn:
            row = conn.execute(
                f"{_CANDIDATE_SELECT} WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        if row is None:
            return None
        return parse_candidate_state_row(tuple(row))

    def list_due_candidates(self, *, as_of: datetime) -> list[CandidateStateRow]:
        with self._connection() as conn:
            rows = conn.execute(
                f"{_CANDIDATE_SELECT} WHERE next_check_at <= ? ORDER BY next_check_at ASC",
                (as_of.isoformat(),),
            ).fetchall()
        return [parse_candidate_state_row(tuple(row)) for row in rows]

    def list_candidates(
        self,
        *,
        limit: int = 50,
        publish_pending_only: bool = False,
    ) -> list[CandidateStateRow]:
        query = _CANDIDATE_SELECT
        params: list[Any] = []
        if publish_pending_only:
            query += " WHERE publish_pending = 1"
        query += " ORDER BY next_check_at ASC LIMIT ?"
        params.append(limit)

        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [parse_candidate_state_row(tuple(row)) for row in rows]

    def get_latest_payload(self, dedupe_key: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT latest_payload FROM candidate_state WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        if row is None:
            return None
        return _parse_json_object(row[0])

    def mark_published(
        self,
        dedupe_key: str,
        *,
        playlist_id: str,
        playlist_uri: str,
        published_at: datetime,
        publish_slot: str | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE candidate_state
                SET publish_pending = 0,
                    spotify_playlist_id = ?,
                    spotify_playlist_uri = ?,
                    last_published_at = ?,
                    spotify_publish_slot = COALESCE(?, spotify_publish_slot)
                WHERE dedupe_key = ?
                """,
                (
                    playlist_id,
                    playlist_uri,
                    published_at.isoformat(),
                    publish_slot,
                    dedupe_key,
                ),
            )

    def record_validation(self, record: ValidationPersistenceRecord) -> bool:
        previous = self.get_candidate_state(record.dedupe_key)
        changed = previous is None or (
            previous.track_uri_hash != record.track_uri_hash
            or previous.effective_status != record.effective_status
            or previous.score_action != record.score_action
        )
        last_change_at = record.checked_at if changed else (
            previous.last_change_at if previous else record.checked_at
        )
        payload_json = payload_to_json(record.payload)
        resolved_uris_json = payload_to_json(list(record.resolved_track_uris))

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO validation_snapshots (
                    dedupe_key,
                    checked_at,
                    effective_status,
                    resolved_track_count,
                    resolved_track_uris,
                    track_uri_hash,
                    payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.dedupe_key,
                    record.checked_at.isoformat(),
                    record.effective_status,
                    record.resolved_track_count,
                    resolved_uris_json,
                    record.track_uri_hash,
                    payload_json,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_state (
                    dedupe_key,
                    source,
                    source_event_id,
                    event_type,
                    artist_name,
                    project_title,
                    event_date,
                    spotify_artist_id,
                    effective_status,
                    artist_value_tier,
                    prerelease_uri,
                    resolved_track_count,
                    resolved_track_uris,
                    track_uri_hash,
                    score_action,
                    last_checked_at,
                    last_change_at,
                    next_check_at,
                    publish_pending,
                    latest_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    source = excluded.source,
                    source_event_id = excluded.source_event_id,
                    event_type = excluded.event_type,
                    artist_name = excluded.artist_name,
                    project_title = excluded.project_title,
                    event_date = excluded.event_date,
                    spotify_artist_id = excluded.spotify_artist_id,
                    effective_status = excluded.effective_status,
                    artist_value_tier = excluded.artist_value_tier,
                    prerelease_uri = excluded.prerelease_uri,
                    resolved_track_count = excluded.resolved_track_count,
                    resolved_track_uris = excluded.resolved_track_uris,
                    track_uri_hash = excluded.track_uri_hash,
                    score_action = excluded.score_action,
                    last_checked_at = excluded.last_checked_at,
                    last_change_at = excluded.last_change_at,
                    next_check_at = excluded.next_check_at,
                    publish_pending = excluded.publish_pending,
                    latest_payload = excluded.latest_payload
                """,
                (
                    record.dedupe_key,
                    record.source,
                    record.source_event_id,
                    record.event_type,
                    record.artist_name,
                    record.project_title,
                    record.event_date.isoformat() if record.event_date else None,
                    record.spotify_artist_id,
                    record.effective_status,
                    record.artist_value_tier,
                    record.prerelease_uri,
                    record.resolved_track_count,
                    resolved_uris_json,
                    record.track_uri_hash,
                    record.score_action,
                    record.checked_at.isoformat(),
                    last_change_at.isoformat() if last_change_at else None,
                    record.next_check_at.isoformat(),
                    int(record.publish_pending),
                    payload_json,
                ),
            )
        return changed

    def get_tier_cache(self, cache_key: str) -> AlbumTierCacheRow | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT cache_key, spotify_artist_id, genius_artist_name,
                       last_popularity, last_follower_count, disposition, reason,
                       checked_at, next_check_at, release_date
                FROM album_tier_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        return _parse_tier_cache_row(tuple(row))

    def get_tier_cache_by_genius_name(self, genius_artist_name: str) -> AlbumTierCacheRow | None:
        normalized = normalize_artist_name(genius_artist_name)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT cache_key, spotify_artist_id, genius_artist_name,
                       last_popularity, last_follower_count, disposition, reason,
                       checked_at, next_check_at, release_date
                FROM album_tier_cache
                WHERE genius_artist_name = ?
                ORDER BY checked_at DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return _parse_tier_cache_row(tuple(row))

    def upsert_tier_cache(
        self,
        *,
        cache_key: str,
        spotify_artist_id: str | None,
        genius_artist_name: str,
        last_popularity: int | None,
        last_follower_count: int | None,
        disposition: str,
        reason: str,
        checked_at: datetime,
        next_check_at: datetime | None,
        release_date: date | None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO album_tier_cache (
                    cache_key,
                    spotify_artist_id,
                    genius_artist_name,
                    last_popularity,
                    last_follower_count,
                    disposition,
                    reason,
                    checked_at,
                    next_check_at,
                    release_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (cache_key) DO UPDATE SET
                    spotify_artist_id = excluded.spotify_artist_id,
                    genius_artist_name = excluded.genius_artist_name,
                    last_popularity = excluded.last_popularity,
                    last_follower_count = excluded.last_follower_count,
                    disposition = excluded.disposition,
                    reason = excluded.reason,
                    checked_at = excluded.checked_at,
                    next_check_at = excluded.next_check_at,
                    release_date = excluded.release_date
                """,
                (
                    cache_key,
                    spotify_artist_id,
                    normalize_artist_name(genius_artist_name),
                    last_popularity,
                    last_follower_count,
                    disposition,
                    reason,
                    checked_at.isoformat(),
                    next_check_at.isoformat() if next_check_at else None,
                    release_date.isoformat() if release_date else None,
                ),
            )

    def delete_tier_cache(self, cache_key: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM album_tier_cache WHERE cache_key = ?", (cache_key,))

    def delete_candidates(self, dedupe_keys: Sequence[str]) -> int:
        """Delete candidate rows and their validation snapshots."""

        keys = [str(key).strip() for key in dedupe_keys if str(key).strip()]
        if not keys:
            return 0
        deleted = 0
        with self._connection() as conn:
            for key in keys:
                conn.execute(
                    "DELETE FROM validation_snapshots WHERE dedupe_key = ?",
                    (key,),
                )
                cursor = conn.execute(
                    "DELETE FROM candidate_state WHERE dedupe_key = ?",
                    (key,),
                )
                deleted += int(cursor.rowcount or 0)
        return deleted

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise TypeError(f"unsupported JSON object value: {value!r}")


def _parse_tier_cache_row(row: tuple[Any, ...]) -> AlbumTierCacheRow:
    release_date_raw = row[9]
    next_check_raw = row[8]
    checked_at_raw = row[7]
    return AlbumTierCacheRow(
        cache_key=str(row[0]),
        spotify_artist_id=str(row[1]) if row[1] else None,
        genius_artist_name=str(row[2]),
        last_popularity=row[3] if isinstance(row[3], int) else None,
        last_follower_count=row[4] if isinstance(row[4], int) else None,
        disposition=str(row[5]),
        reason=str(row[6]),
        checked_at=datetime.fromisoformat(str(checked_at_raw)),
        next_check_at=(
            datetime.fromisoformat(str(next_check_raw)) if next_check_raw else None
        ),
        release_date=date.fromisoformat(str(release_date_raw)) if release_date_raw else None,
    )
