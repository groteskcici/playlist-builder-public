from datetime import date, datetime, timezone
import tempfile
import unittest
from pathlib import Path

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.persistence.sqlite_store import SqliteStore
from playlist_builder.persistence.validation_record import build_validation_record
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
)


class SqliteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SqliteStore.from_path(Path(self._tmpdir.name) / "test.db")
        self.store.init_schema()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_records_and_updates_candidate_state(self) -> None:
        event = NormalizedEvent(
            event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
            source="genius",
            title="Madonna - CONFESSIONS II",
            artist_names=("Madonna",),
            event_date=date(2026, 7, 3),
            raw_payload={"project_title": "CONFESSIONS II"},
            source_event_id="2026-07-03:Madonna:CONFESSIONS II",
        )
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name="Madonna",
            project_title="CONFESSIONS II",
            spotify_artist={"id": "artist_1", "name": "Madonna"},
            artist_value_tier=ArtistValueTier.HIGH,
        )
        checked_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
        record = build_validation_record(
            event=event,
            match=match,
            enrichment={
                "effective_status": "pre_release_candidate",
                "qualified": True,
                "resolution": {
                    "tracks": [{"spotify_track": {"uri": "spotify:track:abc"}}]
                },
            },
            checked_at=checked_at,
            today=date(2026, 6, 17),
        )

        changed = self.store.record_validation(record)
        self.assertTrue(changed)

        state = self.store.get_candidate_state(event.dedupe_key)
        assert state is not None
        self.assertEqual(state.score_action, "queue_for_review")
        self.assertEqual(state.effective_status, "pre_release_candidate")
        self.assertEqual(state.resolved_track_uris, ["spotify:track:abc"])
        self.assertTrue(state.publish_pending)

        unchanged_record = build_validation_record(
            event=event,
            match=match,
            enrichment={
                "effective_status": "pre_release_candidate",
                "qualified": True,
                "resolution": {
                    "tracks": [{"spotify_track": {"uri": "spotify:track:abc"}}]
                },
            },
            checked_at=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
            today=date(2026, 6, 17),
            previous_uris=state.resolved_track_uris,
            previous_status=state.effective_status,
            previous_score_action=state.score_action,
        )
        changed_again = self.store.record_validation(unchanged_record)
        self.assertFalse(changed_again)

        updated = self.store.get_candidate_state(event.dedupe_key)
        assert updated is not None
        self.assertFalse(updated.publish_pending)

    def test_mark_published_clears_publish_pending(self) -> None:
        event = NormalizedEvent(
            event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
            source="genius",
            title="Madonna - CONFESSIONS II",
            artist_names=("Madonna",),
            event_date=date(2026, 7, 3),
            raw_payload={"project_title": "CONFESSIONS II"},
            source_event_id="2026-07-03:Madonna:CONFESSIONS II",
        )
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name="Madonna",
            project_title="CONFESSIONS II",
            spotify_artist={"id": "artist_1", "name": "Madonna"},
            artist_value_tier=ArtistValueTier.HIGH,
        )
        record = build_validation_record(
            event=event,
            match=match,
            enrichment={
                "effective_status": "pre_release_candidate",
                "qualified": True,
                "resolution": {
                    "tracks": [{"spotify_track": {"uri": "spotify:track:abc"}}]
                },
            },
            checked_at=datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc),
            today=date(2026, 6, 17),
        )
        self.store.record_validation(record)
        published_at = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
        self.store.mark_published(
            event.dedupe_key,
            playlist_id="playlist_1",
            playlist_uri="spotify:playlist:playlist_1",
            published_at=published_at,
        )

        state = self.store.get_candidate_state(event.dedupe_key)
        assert state is not None
        self.assertFalse(state.publish_pending)
        self.assertEqual(state.spotify_playlist_id, "playlist_1")
        self.assertEqual(state.spotify_playlist_uri, "spotify:playlist:playlist_1")
        self.assertEqual(state.last_published_at, published_at)


if __name__ == "__main__":
    unittest.main()
