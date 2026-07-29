from datetime import date, datetime, timedelta, timezone
import unittest

from playlist_builder.persistence.album_tier_cache import (
    AlbumTierCacheGate,
    AlbumTierCacheRow,
    TierCacheDisposition,
    classify_tier_cache_disposition,
    compute_tier_cache_next_check_at,
    genius_cache_key,
    tier_cache_should_skip,
)
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
    SpotifyMatcherConfig,
)


class AlbumTierCacheClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SpotifyMatcherConfig()

    def test_high_tier_not_cached(self) -> None:
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name="Madonna",
            project_title="Confessions II",
            spotify_artist={"id": "a1", "name": "Madonna", "popularity": 85},
            artist_value_tier=ArtistValueTier.HIGH,
        )

        self.assertIsNone(
            classify_tier_cache_disposition(match=match, matcher_config=self.config)
        )

    def test_hard_skip_low_popularity(self) -> None:
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ARTIST_TOO_SMALL,
            artist_name="Tiny Artist",
            project_title="Demo",
            spotify_artist={
                "id": "a2",
                "name": "Tiny Artist",
                "popularity": 30,
                "followers": {"total": 12_000},
            },
            artist_value_tier=ArtistValueTier.LOW,
            reason="below popularity threshold",
        )

        disposition, reason = classify_tier_cache_disposition(
            match=match,
            matcher_config=self.config,
        )

        self.assertEqual(disposition, TierCacheDisposition.HARD_SKIP)
        self.assertIn("30", reason)

    def test_watch_borderline_artist(self) -> None:
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ARTIST_TOO_SMALL,
            artist_name="Rising Artist",
            project_title="EP",
            spotify_artist={
                "id": "a3",
                "name": "Rising Artist",
                "popularity": 55,
                "followers": {"total": 900_000},
            },
            artist_value_tier=ArtistValueTier.LOW,
        )

        disposition, _ = classify_tier_cache_disposition(
            match=match,
            matcher_config=self.config,
        )

        self.assertEqual(disposition, TierCacheDisposition.WATCH)

    def test_not_found_disposition(self) -> None:
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ARTIST_NOT_FOUND,
            artist_name="Unknown",
            project_title="Album",
            reason="no match",
        )

        disposition, reason = classify_tier_cache_disposition(
            match=match,
            matcher_config=self.config,
        )

        self.assertEqual(disposition, TierCacheDisposition.NOT_FOUND)
        self.assertIn("no match", reason)


class AlbumTierCacheScheduleTests(unittest.TestCase):
    def test_hard_skip_without_ttl_never_rechecks(self) -> None:
        checked_at = datetime(2026, 6, 17, tzinfo=timezone.utc)
        row = AlbumTierCacheRow(
            cache_key=genius_cache_key("Tiny"),
            spotify_artist_id="a1",
            genius_artist_name="tiny",
            last_popularity=20,
            last_follower_count=1000,
            disposition=TierCacheDisposition.HARD_SKIP.value,
            reason="too small",
            checked_at=checked_at,
            next_check_at=None,
        )

        self.assertTrue(
            tier_cache_should_skip(row, as_of=checked_at + timedelta(days=365))
        )

    def test_watch_rechecks_after_next_check_at(self) -> None:
        checked_at = datetime(2026, 6, 17, tzinfo=timezone.utc)
        next_check = checked_at + timedelta(days=7)
        row = AlbumTierCacheRow(
            cache_key=genius_cache_key("Rising"),
            spotify_artist_id="a2",
            genius_artist_name="rising",
            last_popularity=55,
            last_follower_count=900_000,
            disposition=TierCacheDisposition.WATCH.value,
            reason="borderline",
            checked_at=checked_at,
            next_check_at=next_check,
        )

        self.assertTrue(tier_cache_should_skip(row, as_of=checked_at + timedelta(days=3)))
        self.assertFalse(tier_cache_should_skip(row, as_of=next_check))

    def test_next_check_tightens_near_release(self) -> None:
        checked_at = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
        today = date(2026, 6, 17)
        release = date(2026, 7, 7)

        next_check = compute_tier_cache_next_check_at(
            TierCacheDisposition.WATCH,
            event_date=release,
            today=today,
            checked_at=checked_at,
        )

        self.assertEqual(next_check, checked_at + timedelta(days=3))


class AlbumTierCacheGateTests(unittest.TestCase):
    def test_record_match_deletes_cache_for_high_tier(self) -> None:
        store = _FakeTierCacheStore()
        gate = AlbumTierCacheGate.from_matcher_config(SpotifyMatcherConfig())
        event = _sample_event()
        match = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name="Madonna",
            project_title="Confessions II",
            spotify_artist={"id": "artist_1", "name": "Madonna", "popularity": 85},
            artist_value_tier=ArtistValueTier.HIGH,
        )

        gate.record_match(
            store,
            event,
            match,
            today=date(2026, 6, 17),
            checked_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )

        self.assertEqual(store.deleted_keys, [genius_cache_key("Madonna"), "spotify:artist:artist_1"])
        self.assertEqual(store.rows, {})


class _FakeTierCacheStore:
    def __init__(self) -> None:
        self.rows: dict[str, AlbumTierCacheRow] = {}
        self.deleted_keys: list[str] = []

    def get_tier_cache(self, cache_key: str) -> AlbumTierCacheRow | None:
        return self.rows.get(cache_key)

    def get_tier_cache_by_genius_name(self, genius_artist_name: str) -> AlbumTierCacheRow | None:
        for row in self.rows.values():
            if row.genius_artist_name == genius_artist_name:
                return row
        return None

    def upsert_tier_cache(self, **kwargs) -> None:
        row = AlbumTierCacheRow(
            cache_key=kwargs["cache_key"],
            spotify_artist_id=kwargs.get("spotify_artist_id"),
            genius_artist_name=kwargs["genius_artist_name"],
            last_popularity=kwargs.get("last_popularity"),
            last_follower_count=kwargs.get("last_follower_count"),
            disposition=kwargs["disposition"],
            reason=kwargs["reason"],
            checked_at=kwargs["checked_at"],
            next_check_at=kwargs.get("next_check_at"),
            release_date=kwargs.get("release_date"),
        )
        self.rows[kwargs["cache_key"]] = row

    def delete_tier_cache(self, cache_key: str) -> None:
        self.deleted_keys.append(cache_key)
        self.rows.pop(cache_key, None)


def _sample_event():
    from playlist_builder.events import EventType, NormalizedEvent

    return NormalizedEvent(
        event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
        source="genius",
        title="Madonna - CONFESSIONS II",
        artist_names=("Madonna",),
        event_date=date(2026, 7, 3),
        raw_payload={"project_title": "CONFESSIONS II"},
        source_event_id="2026-07-03:Madonna:CONFESSIONS II",
    )


if __name__ == "__main__":
    unittest.main()
