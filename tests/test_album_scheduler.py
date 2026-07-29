from datetime import date, datetime, timezone
import unittest
from unittest.mock import MagicMock

from playlist_builder.album_pipeline import event_from_payload
from playlist_builder.album_scheduler import AlbumScheduler, _iter_discovery_months
from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.persistence.album_tier_cache import (
    AlbumTierCacheGate,
    AlbumTierCacheRow,
    TierCacheDisposition,
    genius_cache_key,
    normalize_artist_name,
)
from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
    SpotifyMatcherConfig,
)
from playlist_builder.watchers import WatcherResult


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


def sample_row(**overrides) -> CandidateStateRow:
    base = {
        "dedupe_key": "genius:upcoming_album_candidate:2026-07-03:Madonna:CONFESSIONS II",
        "source": "genius",
        "source_event_id": "2026-07-03:Madonna:CONFESSIONS II",
        "event_type": "upcoming_album_candidate",
        "artist_name": "Madonna",
        "project_title": "CONFESSIONS II",
        "event_date": "2026-07-03",
        "spotify_artist_id": "artist_1",
        "effective_status": "pre_release_candidate",
        "artist_value_tier": "high",
        "prerelease_uri": "spotify:prerelease:abc",
        "resolved_track_count": 1,
        "resolved_track_uris": ["spotify:track:abc"],
        "track_uri_hash": "hash",
        "score_action": "queue_for_review",
        "last_checked_at": datetime(2026, 6, 16, tzinfo=timezone.utc),
        "last_change_at": datetime(2026, 6, 16, tzinfo=timezone.utc),
        "next_check_at": datetime(2026, 6, 16, tzinfo=timezone.utc),
        "publish_pending": False,
        "spotify_playlist_id": None,
        "spotify_playlist_uri": None,
        "last_published_at": None,
    }
    base.update(overrides)
    return CandidateStateRow(**base)


class AlbumPipelineTests(unittest.TestCase):
    def test_event_from_payload_roundtrip(self) -> None:
        event = sample_event()
        payload = {"event": event.to_dict()}

        restored = event_from_payload(payload)

        self.assertEqual(restored.dedupe_key, event.dedupe_key)
        self.assertEqual(restored.title, event.title)


class AlbumSchedulerTests(unittest.TestCase):
    def test_refreshes_due_candidates_from_stored_payload(self) -> None:
        event = sample_event()
        row = sample_row(dedupe_key=event.dedupe_key)
        store = MagicMock()
        store.list_due_candidates.return_value = [row]
        store.get_latest_payload.return_value = {"event": event.to_dict()}
        store.get_candidate_state.return_value = row
        store.record_validation.return_value = True

        matcher = MagicMock()
        matcher.validate_album_candidate.return_value = SpotifyMatchResult(
            status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
            artist_name="Madonna",
            project_title="CONFESSIONS II",
            spotify_artist={"id": "artist_1", "name": "Madonna"},
            artist_value_tier=ArtistValueTier.HIGH,
        )

        scheduler = AlbumScheduler(matcher=matcher, enricher=None)
        result = scheduler.run(
            store,
            today=date(2026, 6, 17),
            refresh_due=True,
            discover_genius=False,
            persist=True,
        )

        self.assertEqual(result.summary.refreshed_count, 1)
        matcher.validate_album_candidate.assert_called_once()
        store.record_validation.assert_called_once()

    def test_skips_due_refresh_when_disabled(self) -> None:
        store = MagicMock()
        scheduler = AlbumScheduler(matcher=MagicMock(), enricher=None)

        result = scheduler.run(
            store,
            refresh_due=False,
            discover_genius=False,
            persist=False,
        )

        self.assertEqual(result.summary.refreshed_count, 0)
        store.list_due_candidates.assert_not_called()

    def test_skips_genius_discovery_when_tier_cache_active(self) -> None:
        event = sample_event()
        checked_at = datetime(2026, 6, 17, tzinfo=timezone.utc)
        store = _TierCacheSchedulerStore(
            cached_row=AlbumTierCacheRow(
                cache_key=genius_cache_key("Madonna"),
                spotify_artist_id="artist_1",
                genius_artist_name=normalize_artist_name("Madonna"),
                last_popularity=20,
                last_follower_count=1000,
                disposition=TierCacheDisposition.HARD_SKIP.value,
                reason="too small",
                checked_at=checked_at,
                next_check_at=None,
            )
        )

        matcher = MagicMock()
        gate = AlbumTierCacheGate.from_matcher_config(SpotifyMatcherConfig())
        scheduler = AlbumScheduler(matcher=matcher, enricher=None, tier_cache_gate=gate)
        scheduler._discover_genius = lambda **kwargs: WatcherResult(
            watcher_name="genius_release_calendar",
            events=[event],
            failures=[],
        )

        result = scheduler.run(
            store,
            today=date(2026, 6, 17),
            discover_genius=True,
            refresh_due=False,
            persist=True,
        )

        self.assertEqual(result.summary.discovered_count, 0)
        self.assertEqual(result.summary.tier_cache_skipped, 1)
        matcher.validate_album_candidate.assert_not_called()

    def test_discovers_current_and_future_months(self) -> None:
        event = sample_event()
        store = MagicMock()

        matcher = MagicMock()
        matcher.validate_album_candidate.return_value = SpotifyMatchResult(
            status=SpotifyMatchStatus.ARTIST_TOO_SMALL,
            artist_name="Madonna",
            project_title="CONFESSIONS II",
            spotify_artist={"id": "artist_1", "name": "Madonna"},
            artist_value_tier=ArtistValueTier.LOW,
        )

        scheduler = AlbumScheduler(matcher=matcher, enricher=None)
        seen_months: list[tuple[int, int]] = []

        def fake_discover_genius(*, year: int, month: int, limit: int) -> WatcherResult:
            seen_months.append((year, month))
            return WatcherResult(
                watcher_name="genius_release_calendar",
                events=[event],
                failures=[],
            )

        scheduler._discover_genius = fake_discover_genius

        scheduler.run(
            store,
            today=date(2026, 6, 17),
            discover_genius=True,
            genius_future_months=2,
            refresh_due=False,
            persist=False,
        )

        self.assertEqual(seen_months, [(2026, 6), (2026, 7), (2026, 8)])


class AlbumSchedulerMonthTests(unittest.TestCase):
    def test_iter_discovery_months_rolls_over_year(self) -> None:
        self.assertEqual(
            _iter_discovery_months(2026, 11, 2),
            [(2026, 11), (2026, 12), (2027, 1)],
        )


class _TierCacheSchedulerStore:
    def __init__(self, *, cached_row: AlbumTierCacheRow) -> None:
        self._cached_row = cached_row

    def list_due_candidates(self, *, as_of: datetime) -> list:
        return []

    def get_latest_payload(self, dedupe_key: str):
        return None

    def get_candidate_state(self, dedupe_key: str):
        return None

    def record_validation(self, record) -> bool:
        return True

    def get_tier_cache(self, cache_key: str) -> AlbumTierCacheRow | None:
        if cache_key == self._cached_row.cache_key:
            return self._cached_row
        return None

    def get_tier_cache_by_genius_name(self, genius_artist_name: str) -> AlbumTierCacheRow | None:
        if normalize_artist_name(genius_artist_name) == self._cached_row.genius_artist_name:
            return self._cached_row
        return None

    def upsert_tier_cache(self, **kwargs) -> None:
        return None

    def delete_tier_cache(self, cache_key: str) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
