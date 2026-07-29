from datetime import date, datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from playlist_builder.persistence.sqlite_store import SqliteStore
from playlist_builder.research_import import import_research_files, infer_research_kind


class ResearchImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.store = SqliteStore.from_path(root / "test.db")
        self.store.init_schema()
        self.festival_path = root / "festival.json"
        self.festival_path.write_text(
            json.dumps(
                {
                    "run_date": "2026-07-07",
                    "candidates": [
                        {
                            "event_name": "Mad Cool Festival 2026",
                            "event_type": "festival",
                            "country": "Spain",
                            "city_or_region": "Madrid",
                            "date_start": "2026-07-08",
                            "date_end": "2026-07-11",
                            "primary_artists": ["Foo Fighters", "Lorde"],
                            "notable_supporting_artists": ["JENNIE"],
                            "genre_focus": ["alternative rock", "pop"],
                            "why_it_matters": "Large multi-genre lineup.",
                            "playlist_angle": "Madrid festival playlist.",
                            "suggested_playlist_title": "Mad Cool 2026",
                            "confidence": 0.97,
                            "sources": [
                                {
                                    "title": "Festival lineup",
                                    "url": "https://madcoolfestival.es/lineup",
                                    "source_type": "official",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_infers_kind_and_imports_into_candidate_state(self) -> None:
        checked_at = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
        summary = import_research_files(
            [self.festival_path],
            store=self.store,
            checked_at=checked_at,
            today=date(2026, 7, 7),
        )

        self.assertEqual(summary.imported_file_count, 1)
        self.assertEqual(summary.candidate_count, 1)
        self.assertEqual(summary.changed_count, 1)
        self.assertEqual(summary.archived_file_count, 0)
        self.assertEqual(infer_research_kind(self.festival_path), "festival")

        state = self.store.get_candidate_state(
            "festival_research:festival:2026-07-08:mad-cool-festival-2026"
        )
        assert state is not None
        self.assertEqual(state.effective_status, "research_candidate")
        self.assertEqual(state.score_action, "ready_to_publish")
        self.assertTrue(state.publish_pending)
        self.assertEqual(state.project_title, "Mad Cool Festival 2026")
        self.assertEqual(state.artist_name, "Foo Fighters")
        self.assertEqual(state.next_check_at, checked_at + timedelta(days=1))

        payload = self.store.get_latest_payload(state.dedupe_key)
        assert payload is not None
        self.assertEqual(payload["research_import"]["source_file"], str(self.festival_path))
        self.assertEqual(payload["research_import"]["research_run_date"], "2026-07-07")

    def test_reimport_without_changes_is_not_marked_changed(self) -> None:
        checked_at = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
        first = import_research_files(
            [self.festival_path],
            store=self.store,
            checked_at=checked_at,
            today=date(2026, 7, 7),
        )
        second = import_research_files(
            [self.festival_path],
            store=self.store,
            checked_at=checked_at + timedelta(hours=1),
            today=date(2026, 7, 7),
        )

        self.assertEqual(first.changed_count, 1)
        self.assertEqual(second.changed_count, 0)

    def test_archives_file_after_successful_import(self) -> None:
        archive_dir = Path(self._tmpdir.name) / "imported"
        checked_at = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)

        summary = import_research_files(
            [self.festival_path],
            store=self.store,
            checked_at=checked_at,
            today=date(2026, 7, 7),
            archive_dir=archive_dir,
        )

        self.assertEqual(summary.archived_file_count, 1)
        self.assertEqual(len(summary.archived_paths), 1)
        self.assertFalse(self.festival_path.exists())
        self.assertTrue((archive_dir / "festival.json").exists())


if __name__ == "__main__":
    unittest.main()
