import unittest

from playlist_builder.spotify_ui_scraper import (
    parse_artist_id,
    parse_artist_ui_snapshot,
    parse_prerelease_id,
    parse_prerelease_snapshot,
)


class SpotifyUiScraperTests(unittest.TestCase):
    def test_parse_artist_id_accepts_url_uri_or_id(self) -> None:
        self.assertEqual(
            parse_artist_id("https://open.spotify.com/artist/abc123"),
            "abc123",
        )
        self.assertEqual(parse_artist_id("spotify:artist:def456"), "def456")
        self.assertEqual(parse_artist_id("ghi789"), "ghi789")

    def test_parse_prerelease_id_accepts_url_uri_or_id(self) -> None:
        self.assertEqual(
            parse_prerelease_id("https://open.spotify.com/prerelease/abc123"),
            "abc123",
        )
        self.assertEqual(parse_prerelease_id("spotify:prerelease:def456"), "def456")
        self.assertEqual(parse_prerelease_id("ghi789"), "ghi789")

    def test_parses_monthly_listeners_and_release_countdown(self) -> None:
        text = """
Madonna
54,341,404 monthly listeners
Release countdown CONFESSIONS II Album
"""
        html = '<a draggable="false" href="/prerelease/4ycSWVO8qE6auKr9LTStuC">Countdown</a>'

        snapshot = parse_artist_ui_snapshot(
            artist_id="artist_1",
            url="https://open.spotify.com/artist/artist_1",
            html=html,
            text=text,
            fetcher="fixture",
        )

        self.assertEqual(snapshot.monthly_listeners, 54341404)
        self.assertEqual(len(snapshot.release_countdowns), 1)
        self.assertEqual(snapshot.release_countdowns[0].title, "CONFESSIONS II")
        self.assertEqual(
            snapshot.release_countdowns[0].uri,
            "spotify:prerelease:4ycSWVO8qE6auKr9LTStuC",
        )

    def test_parses_prerelease_tracklist_and_available_tracks(self) -> None:
        text = "\n".join(
            [
                "CONFESSIONS II - Upcoming Album by Madonna | Spotify",
                "CONFESSIONS II",
                "Album",
                "CONFESSIONS II",
                "Madonna • Releases on 3 July 2026",
                "Tracklist preview",
                "#",
                "Title",
                "1",
                "I Feel So Free",
                "Madonna",
                "5:04",
                "2",
                "Good For The Soul",
                "Madonna",
                "-:--",
                "3",
                "Bring Your Love",
                "E",
                "Madonna",
                "Sabrina Carpenter",
                "3:36",
                "© Warner Records",
            ]
        )

        snapshot = parse_prerelease_snapshot(
            prerelease_id="pre_123",
            url="https://open.spotify.com/prerelease/pre_123",
            text=text,
            fetcher="fixture",
        )

        self.assertEqual(snapshot.title, "CONFESSIONS II")
        self.assertEqual(snapshot.artist_name, "Madonna")
        self.assertEqual(snapshot.release_date.isoformat(), "2026-07-03")
        self.assertEqual(len(snapshot.tracks), 3)
        self.assertEqual(len(snapshot.available_tracks), 2)
        self.assertEqual(snapshot.tracks[0].title, "I Feel So Free")
        self.assertTrue(snapshot.tracks[0].is_available)
        self.assertFalse(snapshot.tracks[1].is_available)
        self.assertEqual(
            snapshot.tracks[2].artist_names,
            ("Madonna", "Sabrina Carpenter"),
        )


if __name__ == "__main__":
    unittest.main()
