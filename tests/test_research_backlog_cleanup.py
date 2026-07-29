"""Tests for unpublished research backlog cleanup."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.persistence.sqlite_store import SqliteStore
from playlist_builder.persistence.validation_record import ValidationPersistenceRecord
from playlist_builder.research_backlog_cleanup import (
    clean_research_backlog,
    select_research_backlog,
)


def _row(**overrides: object) -> CandidateStateRow:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    base = {
        "dedupe_key": "festival_research:festival:2026-08-01:demo",
        "source": "festival_research",
        "source_event_id": "2026-08-01:demo",
        "event_type": "festival",
        "artist_name": "Demo Fest",
        "project_title": "Demo Fest 2026",
        "event_date": "2026-08-01",
        "spotify_artist_id": None,
        "effective_status": "research_candidate",
        "artist_value_tier": None,
        "prerelease_uri": None,
        "resolved_track_count": 0,
        "resolved_track_uris": [],
        "track_uri_hash": "hash",
        "score_action": "ready_to_publish",
        "last_checked_at": now,
        "last_change_at": now,
        "next_check_at": now,
        "publish_pending": True,
        "spotify_playlist_id": None,
        "spotify_playlist_uri": None,
        "last_published_at": None,
    }
    base.update(overrides)
    return CandidateStateRow(**base)  # type: ignore[arg-type]


class ResearchBacklogCleanupTests(unittest.TestCase):
    def test_select_skips_published_and_old_rows(self) -> None:
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        cutoff = now - timedelta(days=3)
        rows = [
            _row(dedupe_key="fresh", last_change_at=now - timedelta(days=1)),
            _row(
                dedupe_key="old",
                last_change_at=now - timedelta(days=10),
                last_checked_at=now - timedelta(days=10),
            ),
            _row(
                dedupe_key="published",
                spotify_playlist_id="pl1",
                last_published_at=now,
            ),
            _row(
                dedupe_key="album",
                source="genius",
                event_type="upcoming_album_candidate",
            ),
        ]

        selected = select_research_backlog(rows, cutoff=cutoff)
        self.assertEqual([row.dedupe_key for row in selected], ["fresh"])

    def test_clean_research_backlog_apply_deletes_rows_and_pending(self) -> None:
        now = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.db"
            pending = root / "pending"
            pending.mkdir()
            (pending / "festival_2026-07-23.json").write_text("{}", encoding="utf-8")
            (pending / "notes.txt").write_text("keep", encoding="utf-8")

            store = SqliteStore.from_path(db_path)
            store.init_schema()
            store.record_validation(
                ValidationPersistenceRecord(
                    dedupe_key="festival_research:festival:2026-08-01:demo",
                    source="festival_research",
                    source_event_id="2026-08-01:demo",
                    event_type="festival",
                    artist_name="Demo Fest",
                    project_title="Demo Fest 2026",
                    event_date=None,
                    spotify_artist_id=None,
                    effective_status="research_candidate",
                    artist_value_tier=None,
                    prerelease_uri=None,
                    resolved_track_uris=(),
                    score_action="ready_to_publish",
                    checked_at=now - timedelta(days=1),
                    next_check_at=now,
                    publish_pending=True,
                    payload={"event": {"raw_payload": {"suggested_playlist_title": "Demo"}}},
                )
            )
            store.record_validation(
                ValidationPersistenceRecord(
                    dedupe_key="genius:album:keep",
                    source="genius",
                    source_event_id="keep",
                    event_type="upcoming_album_candidate",
                    artist_name="Keep",
                    project_title="Album",
                    event_date=None,
                    spotify_artist_id=None,
                    effective_status="pre_release_candidate",
                    artist_value_tier="high",
                    prerelease_uri=None,
                    resolved_track_uris=(),
                    score_action="queue_for_review",
                    checked_at=now,
                    next_check_at=now,
                    publish_pending=True,
                    payload={},
                )
            )

            dry = clean_research_backlog(
                store,
                days=3,
                apply=False,
                pending_dir=pending,
                now=now,
            )
            self.assertTrue(dry.dry_run)
            self.assertEqual(dry.matched_count, 1)
            self.assertEqual(dry.deleted_count, 0)
            self.assertTrue((pending / "festival_2026-07-23.json").is_file())

            applied = clean_research_backlog(
                store,
                days=3,
                apply=True,
                pending_dir=pending,
                now=now,
            )
            self.assertFalse(applied.dry_run)
            self.assertEqual(applied.deleted_count, 1)
            self.assertEqual(applied.pending_cleared_count, 1)
            self.assertIsNone(
                store.get_candidate_state("festival_research:festival:2026-08-01:demo")
            )
            self.assertIsNotNone(store.get_candidate_state("genius:album:keep"))
            self.assertFalse((pending / "festival_2026-07-23.json").exists())
            self.assertTrue((pending / "notes.txt").is_file())


if __name__ == "__main__":
    unittest.main()
