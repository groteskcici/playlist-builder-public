from datetime import date
import os
import unittest
from unittest.mock import patch

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.scoring import ScoreAction, ScoringConfig, score_opportunity
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


def high_match(*, status: SpotifyMatchStatus, album=None) -> SpotifyMatchResult:
    return SpotifyMatchResult(
        status=status,
        artist_name="Madonna",
        project_title="CONFESSIONS II",
        spotify_artist={"id": "artist_1", "name": "Madonna"},
        spotify_album=album,
        artist_value_tier=ArtistValueTier.HIGH,
    )


class ScoringTests(unittest.TestCase):
    def test_ignores_low_tier_artists(self) -> None:
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ARTIST_TOO_SMALL,
            artist_name="Unknown",
            project_title="Demo",
            artist_value_tier=ArtistValueTier.LOW,
        )

        score = score_opportunity(
            event=sample_event(),
            match=match,
            enrichment=None,
            today=date(2026, 6, 17),
        )

        self.assertEqual(score.action, ScoreAction.IGNORE)

    def test_ignores_unreleased_album_without_prerelease(self) -> None:
        score = score_opportunity(
            event=sample_event(),
            match=high_match(status=SpotifyMatchStatus.ALBUM_NOT_RELEASED),
            enrichment=None,
            today=date(2026, 6, 17),
        )

        self.assertEqual(score.action, ScoreAction.IGNORE)
        self.assertEqual(score.effective_status, "album_not_released")

    def test_queues_qualified_pre_release_for_review(self) -> None:
        score = score_opportunity(
            event=sample_event(),
            match=high_match(status=SpotifyMatchStatus.ALBUM_NOT_RELEASED),
            enrichment={
                "effective_status": "pre_release_candidate",
                "qualified": True,
                "resolution": {
                    "tracks": [{"spotify_track": {"uri": "spotify:track:abc"}}]
                },
            },
            today=date(2026, 6, 17),
        )

        self.assertEqual(score.action, ScoreAction.QUEUE_FOR_REVIEW)
        self.assertEqual(score.resolved_track_count, 1)

    def test_ready_to_publish_high_tier_confirmed_album(self) -> None:
        album = {
            "id": "album_1",
            "name": "CONFESSIONS II",
            "release_date": "2026-06-15",
            "album_type": "album",
            "total_tracks": 12,
        }
        with patch.dict(os.environ, {"ALBUM_INCLUDE_CONFIRMED_DROPS": "1"}):
            score = score_opportunity(
                event=sample_event(),
                match=high_match(status=SpotifyMatchStatus.ALBUM_CONFIRMED, album=album),
                enrichment=None,
                today=date(2026, 6, 17),
            )

        self.assertEqual(score.action, ScoreAction.READY_TO_PUBLISH)

    def test_queues_review_tier_confirmed_album(self) -> None:
        album = {
            "id": "album_1",
            "name": "SPLAT!",
            "release_date": "2026-06-15",
            "album_type": "album",
            "total_tracks": 12,
        }
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_CONFIRMED,
            artist_name="Deep Purple",
            project_title="SPLAT!",
            spotify_artist={"id": "artist_2", "name": "Deep Purple"},
            spotify_album=album,
            artist_value_tier=ArtistValueTier.REVIEW,
        )

        with patch.dict(os.environ, {"ALBUM_INCLUDE_CONFIRMED_DROPS": "1"}):
            score = score_opportunity(
                event=sample_event(),
                match=match,
                enrichment=None,
                today=date(2026, 6, 17),
            )

        self.assertEqual(score.action, ScoreAction.QUEUE_FOR_REVIEW)

    def test_ignores_stale_confirmed_album(self) -> None:
        album = {
            "id": "album_1",
            "name": "Old Album",
            "release_date": "2026-05-01",
            "album_type": "album",
            "total_tracks": 12,
        }
        with patch.dict(os.environ, {"ALBUM_INCLUDE_CONFIRMED_DROPS": "1"}):
            score = score_opportunity(
                event=sample_event(),
                match=high_match(status=SpotifyMatchStatus.ALBUM_CONFIRMED, album=album),
                enrichment=None,
                today=date(2026, 6, 17),
                config=ScoringConfig(max_album_age_days=7),
            )

        self.assertEqual(score.action, ScoreAction.IGNORE)


if __name__ == "__main__":
    unittest.main()
