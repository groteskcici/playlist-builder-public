from datetime import date, datetime, timezone
import os
import unittest
from unittest.mock import patch

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.persistence.refresh import compute_next_check_at, track_uri_hash
from playlist_builder.persistence.resolved_tracks import extract_resolved_track_uris
from playlist_builder.persistence.validation_record import build_validation_record
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
)


def sample_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
        source="genius",
        title="Madonna - CONFESSIONS II",
        artist_names=("Madonna",),
        event_date=date(2026, 7, 3),
        raw_payload={"project_title": "CONFESSIONS II"},
        source_event_id="2026-07-03:Madonna:CONFESSIONS II",
    )


def sample_match() -> SpotifyMatchResult:
    return SpotifyMatchResult(
        status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
        artist_name="Madonna",
        project_title="CONFESSIONS II",
        spotify_artist={"id": "artist_1", "name": "Madonna"},
        artist_value_tier=ArtistValueTier.HIGH,
    )


class PersistenceRecordTests(unittest.TestCase):
    def test_extract_resolved_track_uris_from_enrichment_dict(self) -> None:
        enrichment = {
            "qualified": True,
            "resolution": {
                "tracks": [
                    {
                        "spotify_track": {"uri": "spotify:track:abc"},
                    },
                    {
                        "spotify_track": {"uri": "spotify:track:def"},
                    },
                    {
                        "spotify_track": None,
                    },
                ]
            },
        }

        self.assertEqual(
            extract_resolved_track_uris(enrichment),
            ["spotify:track:abc", "spotify:track:def"],
        )

    def test_build_validation_record_marks_publish_pending_on_first_qualified_hit(
        self,
    ) -> None:
        checked_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
        record = build_validation_record(
            event=sample_event(),
            match=sample_match(),
            enrichment={
                "effective_status": "pre_release_candidate",
                "qualified": True,
                "prerelease_uri": "spotify:prerelease:pre_123",
                "resolution": {
                    "tracks": [
                        {"spotify_track": {"uri": "spotify:track:abc"}},
                    ]
                },
            },
            checked_at=checked_at,
            today=date(2026, 6, 17),
        )

        self.assertTrue(record.publish_pending)
        self.assertEqual(record.score_action, "queue_for_review")
        self.assertEqual(record.effective_status, "pre_release_candidate")
        self.assertEqual(record.resolved_track_uris, ("spotify:track:abc",))
        self.assertEqual(record.track_uri_hash, track_uri_hash(["spotify:track:abc"]))

    def test_build_validation_record_skips_publish_pending_when_unchanged(
        self,
    ) -> None:
        checked_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
        enrichment = {
            "effective_status": "pre_release_candidate",
            "qualified": True,
            "resolution": {
                "tracks": [{"spotify_track": {"uri": "spotify:track:abc"}}]
            },
        }
        record = build_validation_record(
            event=sample_event(),
            match=sample_match(),
            enrichment=enrichment,
            checked_at=checked_at,
            today=date(2026, 6, 17),
            previous_uris=["spotify:track:abc"],
            previous_status="pre_release_candidate",
            previous_score_action="queue_for_review",
        )

        self.assertFalse(record.publish_pending)

    def test_build_validation_record_marks_album_confirmed_ready_to_publish(
        self,
    ) -> None:
        checked_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_CONFIRMED,
            artist_name="Madonna",
            project_title="CONFESSIONS II",
            spotify_artist={"id": "artist_1", "name": "Madonna"},
            spotify_album={
                "id": "album_1",
                "name": "CONFESSIONS II",
                "release_date": "2026-06-15",
                "album_type": "album",
                "total_tracks": 12,
            },
            artist_value_tier=ArtistValueTier.HIGH,
        )
        with patch.dict(os.environ, {"ALBUM_INCLUDE_CONFIRMED_DROPS": "1"}):
            record = build_validation_record(
                event=sample_event(),
                match=match,
                enrichment=None,
                checked_at=checked_at,
                today=date(2026, 6, 17),
            )

        self.assertEqual(record.score_action, "ready_to_publish")
        self.assertTrue(record.publish_pending)

    def test_compute_next_check_at_inside_release_window(self) -> None:
        result = compute_next_check_at(
            event_date=date(2026, 6, 20),
            today=date(2026, 6, 17),
            effective_status="pre_release_candidate",
        )
        self.assertGreater(result, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
