from datetime import date
import unittest

from playlist_builder.spotify_prerelease_resolver import (
    SpotifyPrereleaseResolverConfig,
    SpotifyPrereleaseTrackResolver,
)
from playlist_builder.spotify_ui_scraper import (
    SpotifyPrereleaseSnapshot,
    SpotifyPrereleaseTrack,
)


class FakeSpotifyTrackSearchClient:
    def __init__(self, tracks=None) -> None:
        self.tracks = tracks or []
        self.queries = []

    def search_tracks(self, query: str, *, limit: int = 10, market: str | None = None):
        self.queries.append((query, limit, market))
        return self.tracks


def source_track(
    title: str = "Bring Your Love",
    *,
    duration: str = "3:36",
    artists: tuple[str, ...] = ("Madonna", "Sabrina Carpenter"),
) -> SpotifyPrereleaseTrack:
    return SpotifyPrereleaseTrack(
        position=4,
        title=title,
        artist_names=artists,
        duration=duration,
        is_available=True,
    )


def snapshot(track: SpotifyPrereleaseTrack) -> SpotifyPrereleaseSnapshot:
    return SpotifyPrereleaseSnapshot(
        prerelease_id="pre_123",
        url="https://open.spotify.com/prerelease/pre_123",
        title="CONFESSIONS II",
        artist_name="Madonna",
        release_date=date(2026, 7, 3),
        tracks=(track,),
        fetcher="fixture",
    )


def spotify_track(
    name: str = "Bring Your Love",
    *,
    duration_ms: int = 216_000,
    artist_name: str = "Madonna",
):
    return {
        "id": "track_1",
        "name": name,
        "uri": "spotify:track:track_1",
        "duration_ms": duration_ms,
        "artists": [{"id": "artist_1", "name": artist_name, "uri": "spotify:artist:artist_1"}],
        "album": {
            "id": "album_1",
            "name": "CONFESSIONS II",
            "uri": "spotify:album:album_1",
            "release_date": "2026-06-12",
        },
        "external_urls": {"spotify": "https://open.spotify.com/track/track_1"},
    }


class SpotifyPrereleaseTrackResolverTests(unittest.TestCase):
    def test_resolves_available_prerelease_track_to_spotify_uri(self) -> None:
        client = FakeSpotifyTrackSearchClient(tracks=[spotify_track()])
        resolver = SpotifyPrereleaseTrackResolver(
            client,
            SpotifyPrereleaseResolverConfig(market="DE"),
        )

        result = resolver.resolve(snapshot(source_track()))

        self.assertEqual(len(result.matched_tracks), 1)
        self.assertEqual(result.matched_tracks[0].spotify_track["uri"], "spotify:track:track_1")
        self.assertEqual(client.queries[0][0], 'track:"Bring Your Love" artist:"Madonna"')
        self.assertEqual(client.queries[0][2], "DE")
        self.assertEqual(result.to_dict()["matched_count"], 1)

    def test_rejects_duration_mismatch(self) -> None:
        resolver = SpotifyPrereleaseTrackResolver(
            FakeSpotifyTrackSearchClient(tracks=[spotify_track(duration_ms=120_000)]),
        )

        result = resolver.resolve(snapshot(source_track()))

        self.assertEqual(len(result.matched_tracks), 0)
        self.assertEqual(result.tracks[0].reason, "best candidate duration differed too much")

    def test_reports_unmatched_search_result(self) -> None:
        resolver = SpotifyPrereleaseTrackResolver(FakeSpotifyTrackSearchClient(tracks=[]))

        result = resolver.resolve(snapshot(source_track()))

        self.assertEqual(len(result.unmatched_tracks), 1)
        self.assertEqual(
            result.unmatched_tracks[0].reason,
            "Spotify search returned no track candidates",
        )

    def test_config_validates_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "search_limit"):
            SpotifyPrereleaseResolverConfig(search_limit=0)
        with self.assertRaisesRegex(ValueError, "min_title_similarity"):
            SpotifyPrereleaseResolverConfig(min_title_similarity=1.1)


if __name__ == "__main__":
    unittest.main()
