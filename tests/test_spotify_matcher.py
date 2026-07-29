from datetime import date
import unittest

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.spotify_matcher import (
    SpotifyMatcher,
    SpotifyMatcherConfig,
    SpotifyMatchStatus,
)


class FakeSpotifyMatcherClient:
    def __init__(self, *, artists=None, albums=None, tracks=None) -> None:
        self.artists = artists or []
        self.albums = albums or []
        self.tracks = tracks or []
        self.artist_queries = []
        self.album_queries = []
        self.track_queries = []

    def search_artists(self, query: str, *, limit: int = 5, market: str | None = None):
        self.artist_queries.append((query, limit, market))
        return self.artists

    def search_albums(self, query: str, *, limit: int = 10, market: str | None = None):
        self.album_queries.append((query, limit, market))
        return self.albums

    def search_tracks(self, query: str, *, limit: int = 10, market: str | None = None):
        self.track_queries.append((query, limit, market))
        return self.tracks


def artist(name: str, *, popularity: int = 90, artist_id: str = "artist_1"):
    return {
        "id": artist_id,
        "name": name,
        "popularity": popularity,
        "followers": {"total": 10_000_000},
        "uri": f"spotify:artist:{artist_id}",
    }


def album(
    name: str,
    artist_name: str,
    *,
    album_type: str = "album",
    release_date: str = "2026-07-03",
    total_tracks: int = 12,
    artist_id: str = "artist_1",
):
    return {
        "id": "album_1",
        "name": name,
        "album_type": album_type,
        "release_date": release_date,
        "release_date_precision": "day",
        "total_tracks": total_tracks,
        "uri": "spotify:album:album_1",
        "artists": [{"id": artist_id, "name": artist_name}],
    }


def track(
    name: str,
    album_name: str,
    artist_name: str,
    *,
    track_id: str = "track_1",
    artist_id: str = "artist_1",
):
    return {
        "id": track_id,
        "name": name,
        "uri": f"spotify:track:{track_id}",
        "artists": [{"id": artist_id, "name": artist_name}],
        "album": {
            "id": "album_1",
            "name": album_name,
            "release_date": "2026-06-01",
        },
    }


def upcoming_event():
    return NormalizedEvent(
        event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
        source="genius",
        title="Madonna - CONFESSIONS II",
        artist_names=("Madonna",),
        event_date=date(2026, 7, 3),
        raw_payload={"project_title": "CONFESSIONS II"},
    )


class SpotifyMatcherTests(unittest.TestCase):
    def test_confirms_released_album_for_popular_artist(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[artist("Madonna")],
                albums=[album("CONFESSIONS II", "Madonna", release_date="2026-07-03")],
            )
        )

        result = matcher.validate_album_candidate(
            upcoming_event(),
            today=date(2026, 7, 4),
        )

        self.assertEqual(result.status, SpotifyMatchStatus.ALBUM_CONFIRMED)
        self.assertTrue(result.is_actionable)
        self.assertEqual(result.album_kind, "album")
        self.assertEqual(result.to_dict()["spotify_artist"]["popularity"], 90)

    def test_future_album_page_alone_is_not_actionable(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[artist("Madonna")],
                albums=[album("CONFESSIONS II", "Madonna", release_date="2026-07-03")],
            )
        )

        result = matcher.validate_album_candidate(
            upcoming_event(),
            today=date(2026, 6, 20),
        )

        self.assertEqual(result.status, SpotifyMatchStatus.ALBUM_NOT_RELEASED)
        self.assertFalse(result.is_actionable)
        self.assertIn("official track URIs", result.reason or "")

    def test_rejects_artist_below_popularity_threshold(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[
                    {
                        **artist("Madonna", popularity=50),
                        "followers": {"total": 100_000},
                    }
                ]
            ),
            SpotifyMatcherConfig(high_value_popularity=80),
        )

        result = matcher.validate_album_candidate(upcoming_event())

        self.assertEqual(result.status, SpotifyMatchStatus.ARTIST_TOO_SMALL)
        self.assertFalse(result.is_actionable)
        self.assertEqual(result.spotify_album, None)

    def test_review_tier_artist_continues_to_album_matching(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[
                    {
                        **artist("Deep Purple", popularity=66),
                        "followers": {"total": 6_000_000},
                    }
                ],
                albums=[],
            ),
            SpotifyMatcherConfig(high_value_popularity=80),
        )
        event = NormalizedEvent(
            event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
            source="genius",
            title="Deep Purple - SPLAT!",
            artist_names=("Deep Purple",),
            event_date=date(2026, 7, 3),
            raw_payload={"project_title": "SPLAT!"},
        )

        result = matcher.validate_album_candidate(event)

        self.assertEqual(result.status, SpotifyMatchStatus.ALBUM_NOT_RELEASED)
        self.assertEqual(result.to_dict()["artist_value_tier"], "review")

    def test_marks_missing_album_as_not_released(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(artists=[artist("Madonna")], albums=[]),
        )

        result = matcher.validate_album_candidate(upcoming_event())

        self.assertEqual(result.status, SpotifyMatchStatus.ALBUM_NOT_RELEASED)
        self.assertFalse(result.is_actionable)

    def test_marks_available_project_tracks_as_pre_release_candidate(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[artist("Madonna")],
                albums=[],
                tracks=[
                    track("Lead Single", "CONFESSIONS II", "Madonna", track_id="track_1"),
                ],
            )
        )

        result = matcher.validate_album_candidate(
            upcoming_event(),
            today=date(2026, 6, 20),
        )

        self.assertEqual(result.status, SpotifyMatchStatus.PRE_RELEASE_CANDIDATE)
        self.assertTrue(result.is_actionable)
        self.assertEqual(len(result.available_tracks), 1)
        self.assertEqual(result.to_dict()["available_tracks"][0]["name"], "Lead Single")

    def test_marks_available_project_tracks_for_far_future_release(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[artist("Madonna")],
                albums=[],
                tracks=[
                    track("Lead Single", "CONFESSIONS II", "Madonna", track_id="track_1"),
                ],
            )
        )

        result = matcher.validate_album_candidate(
            upcoming_event(),
            today=date(2026, 1, 1),
        )

        self.assertEqual(result.status, SpotifyMatchStatus.PRE_RELEASE_CANDIDATE)
        self.assertTrue(result.is_actionable)

    def test_review_tier_requires_multiple_pre_release_tracks(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(
                artists=[
                    {
                        **artist("Deep Purple", popularity=66),
                        "followers": {"total": 6_000_000},
                    }
                ],
                albums=[],
                tracks=[
                    track("Lead Single", "SPLAT!", "Deep Purple", track_id="track_1"),
                ],
            )
        )
        event = NormalizedEvent(
            event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
            source="genius",
            title="Deep Purple - SPLAT!",
            artist_names=("Deep Purple",),
            event_date=date(2026, 7, 3),
            raw_payload={"project_title": "SPLAT!"},
        )

        result = matcher.validate_album_candidate(event, today=date(2026, 6, 20))

        self.assertEqual(result.status, SpotifyMatchStatus.ALBUM_NOT_RELEASED)

    def test_rejects_weak_artist_match(self) -> None:
        matcher = SpotifyMatcher(
            FakeSpotifyMatcherClient(artists=[artist("Not Madonna")]),
        )

        result = matcher.validate_album_candidate(upcoming_event())

        self.assertEqual(result.status, SpotifyMatchStatus.ARTIST_NOT_FOUND)

    def test_config_validates_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "high_value_popularity"):
            SpotifyMatcherConfig(high_value_popularity=101)

        with self.assertRaisesRegex(ValueError, "review_value_popularity"):
            SpotifyMatcherConfig(review_value_popularity=101)

        with self.assertRaisesRegex(ValueError, "min_title_similarity"):
            SpotifyMatcherConfig(min_title_similarity=1.1)

        with self.assertRaisesRegex(ValueError, "search_limit"):
            SpotifyMatcherConfig(search_limit=0)


if __name__ == "__main__":
    unittest.main()
