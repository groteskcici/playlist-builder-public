from datetime import date
import unittest

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.spotify_matcher import (
    ArtistValueTier,
    SpotifyMatchResult,
    SpotifyMatchStatus,
)
from playlist_builder.spotify_prerelease_enrichment import (
    SpotifyPrereleaseEnricher,
    SpotifyPrereleaseEnrichmentConfig,
)
from playlist_builder.spotify_prerelease_resolver import (
    ResolvedPrereleaseTrack,
    SpotifyPrereleaseResolution,
)
from playlist_builder.spotify_ui_scraper import (
    SpotifyArtistUiSnapshot,
    SpotifyPrereleaseSnapshot,
    SpotifyPrereleaseTrack,
    SpotifyReleaseCountdown,
)


class FakeUiClient:
    def __init__(self, *, countdown_title: str = "CONFESSIONS II") -> None:
        self.countdown_title = countdown_title
        self.artist_requests = []
        self.prerelease_requests = []

    def scrape_artist(self, artist: str) -> SpotifyArtistUiSnapshot:
        self.artist_requests.append(artist)
        return SpotifyArtistUiSnapshot(
            artist_id=artist,
            url=f"https://open.spotify.com/artist/{artist}",
            monthly_listeners=54_000_000,
            release_countdowns=(
                SpotifyReleaseCountdown(
                    title=self.countdown_title,
                    uri="spotify:prerelease:pre_123",
                ),
            ),
            fetcher="fixture",
        )

    def scrape_prerelease(self, prerelease: str) -> SpotifyPrereleaseSnapshot:
        self.prerelease_requests.append(prerelease)
        track = SpotifyPrereleaseTrack(
            position=1,
            title="I Feel So Free",
            artist_names=("Madonna",),
            duration="5:04",
            is_available=True,
        )
        return SpotifyPrereleaseSnapshot(
            prerelease_id="pre_123",
            url="https://open.spotify.com/prerelease/pre_123",
            title="CONFESSIONS II",
            artist_name="Madonna",
            release_date=date(2026, 7, 3),
            tracks=(track,),
            fetcher="fixture",
        )


class FakeResolver:
    def __init__(self, *, matched_count: int = 1) -> None:
        self.matched_count = matched_count

    def resolve(self, snapshot: SpotifyPrereleaseSnapshot) -> SpotifyPrereleaseResolution:
        tracks = []
        for index in range(self.matched_count):
            source_track = snapshot.available_tracks[min(index, len(snapshot.available_tracks) - 1)]
            tracks.append(
                ResolvedPrereleaseTrack(
                    source_track=source_track,
                    spotify_track={
                        "id": f"track_{index}",
                        "name": source_track.title,
                        "uri": f"spotify:track:track_{index}",
                    },
                    query=f'track:"{source_track.title}" artist:"Madonna"',
                    title_similarity=1.0,
                    artist_similarity=1.0,
                    reason="fixture match",
                )
            )
        return SpotifyPrereleaseResolution(prerelease=snapshot, tracks=tuple(tracks))


def event() -> NormalizedEvent:
    return NormalizedEvent(
        event_type=EventType.UPCOMING_ALBUM_CANDIDATE,
        source="genius",
        title="Madonna - CONFESSIONS II",
        artist_names=("Madonna",),
        event_date=date(2026, 7, 3),
        raw_payload={"project_title": "CONFESSIONS II"},
    )


def base_match(*, tier: ArtistValueTier = ArtistValueTier.HIGH) -> SpotifyMatchResult:
    return SpotifyMatchResult(
        status=SpotifyMatchStatus.ALBUM_NOT_RELEASED,
        artist_name="Madonna",
        project_title="CONFESSIONS II",
        spotify_artist={
            "id": "artist_1",
            "name": "Madonna",
            "popularity": 90,
            "followers": {"total": 10_000_000},
        },
        artist_similarity=1.0,
        artist_value_tier=tier,
    )


class SpotifyPrereleaseEnricherTests(unittest.TestCase):
    def test_qualifies_high_tier_with_one_resolved_track_uri(self) -> None:
        enricher = SpotifyPrereleaseEnricher(FakeUiClient(), FakeResolver(matched_count=1))

        result = enricher.enrich(event(), base_match(), today=date(2026, 6, 20))

        self.assertTrue(result.qualified)
        self.assertEqual(result.effective_status, SpotifyMatchStatus.PRE_RELEASE_CANDIDATE)
        self.assertEqual(result.prerelease_uri, "spotify:prerelease:pre_123")
        self.assertEqual(result.to_dict()["resolved_track_count"], 1)

    def test_review_tier_requires_two_resolved_track_uris(self) -> None:
        enricher = SpotifyPrereleaseEnricher(
            FakeUiClient(),
            FakeResolver(matched_count=1),
        )

        result = enricher.enrich(
            event(),
            base_match(tier=ArtistValueTier.REVIEW),
            today=date(2026, 6, 20),
        )

        self.assertFalse(result.qualified)
        self.assertEqual(result.effective_status, SpotifyMatchStatus.ALBUM_NOT_RELEASED)
        self.assertIn("needed 2", result.reason or "")

    def test_skips_past_release_date(self) -> None:
        enricher = SpotifyPrereleaseEnricher(FakeUiClient(), FakeResolver())

        result = enricher.enrich(event(), base_match(), today=date(2026, 7, 10))

        self.assertFalse(result.qualified)
        self.assertEqual(
            result.reason,
            "event date is outside prerelease enrichment window",
        )

    def test_enriches_far_future_release_when_singles_exist(self) -> None:
        enricher = SpotifyPrereleaseEnricher(FakeUiClient(), FakeResolver(matched_count=1))

        result = enricher.enrich(event(), base_match(), today=date(2026, 1, 1))

        self.assertTrue(result.qualified)
        self.assertEqual(result.effective_status, SpotifyMatchStatus.PRE_RELEASE_CANDIDATE)

    def test_config_validates_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_countdown_title_similarity"):
            SpotifyPrereleaseEnrichmentConfig(min_countdown_title_similarity=1.1)


if __name__ == "__main__":
    unittest.main()
