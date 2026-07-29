from datetime import date
import unittest

from playlist_builder.genius_release_calendar import (
    GeniusReleaseCalendarConfig,
    parse_calendar_entries,
)


class GeniusReleaseCalendarTests(unittest.TestCase):
    def test_parses_calendar_entries_from_lyrics_containers(self) -> None:
        html = """
<div data-lyrics-container="true">
WELCOME TO GENIUS' JULY 2026 ALBUM RELEASE CALENDAR!!! Read More 7/3<br/>
Madonna - CONFESSIONS II - 8/16<br/>
Panda Bear & Sonic Boom - A ? of When<br/>
7/10<br/>
Kelela - new avatar - 2/12
</div>
"""

        entries = parse_calendar_entries(html, year=2026, month=7)

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].release_date, date(2026, 7, 3))
        self.assertEqual(entries[0].artist_name, "Madonna")
        self.assertEqual(entries[0].project_title, "CONFESSIONS II")
        self.assertEqual(entries[0].posted_tracks, 8)
        self.assertEqual(entries[0].total_tracks, 16)
        self.assertEqual(entries[1].artist_name, "Panda Bear & Sonic Boom")
        self.assertEqual(entries[1].project_title, "A ? of When")
        self.assertIsNone(entries[1].posted_tracks)
        self.assertEqual(entries[2].release_date, date(2026, 7, 10))

    def test_skips_bad_line_and_keeps_valid_entries(self) -> None:
        html = """
<div data-lyrics-container="true">
6/20<br/>
Madonna - CONFESSIONS II - 8/16<br/>
6/31<br/>
Kelela - new avatar - 2/12
</div>
"""
        failures: list[str] = []
        entries = parse_calendar_entries(html, year=2026, month=6, failures=failures)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].artist_name, "Madonna")
        self.assertEqual(entries[1].artist_name, "Kelela")
        self.assertEqual(len(failures), 1)
        self.assertIn("6/31", failures[0])

    def test_config_validates_month(self) -> None:
        with self.assertRaisesRegex(ValueError, "month"):
            GeniusReleaseCalendarConfig(year=2026, month=0)

        with self.assertRaisesRegex(ValueError, "month"):
            GeniusReleaseCalendarConfig(year=2026, month=13)


if __name__ == "__main__":
    unittest.main()
