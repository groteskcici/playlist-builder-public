import unittest
from datetime import date

from playlist_builder.events import EventType
from playlist_builder.moment_research import MomentResearchResult


class MomentResearchTests(unittest.TestCase):
    def test_parses_agent_result_and_converts_to_event(self) -> None:
        result = MomentResearchResult.from_dict(
            {
                "run_date": "2026-07-07",
                "candidates": [
                    {
                        "event_name": "Super Bowl Halftime Show 2026",
                        "event_type": "moment",
                        "moment_type": "halftime_show",
                        "country": "United States",
                        "city_or_region": "Santa Clara",
                        "date_start": "2026-02-08",
                        "date_end": "2026-02-08",
                        "related_artists": ["Kendrick Lamar", "SZA"],
                        "genre_focus": ["hip-hop", "r&b"],
                        "music_connection": "Massive live TV music moment with catalog spillover.",
                        "why_it_matters": "Creates immediate streaming demand and discovery.",
                        "playlist_angle": "Halftime setlist + related catalog playlist.",
                        "suggested_playlist_title": "Super Bowl Halftime 2026",
                        "confidence": 0.98,
                        "sources": [
                            {
                                "title": "NFL announcement",
                                "url": "https://www.nfl.com/news/example",
                                "source_type": "official",
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(result.run_date, date(2026, 7, 7))
        event = result.to_events()[0]
        self.assertEqual(event.event_type, EventType.MOMENT)
        self.assertEqual(event.title, "Super Bowl Halftime Show 2026")
        self.assertEqual(event.artist_names, ("Kendrick Lamar", "SZA"))
        self.assertEqual(
            event.source_event_id,
            "2026-02-08:halftime-show:super-bowl-halftime-show-2026",
        )

    def test_rejects_more_than_daily_cap(self) -> None:
        candidates = []
        for index in range(6):
            candidates.append(
                {
                    "event_name": f"Example Moment {index}",
                    "event_type": "moment",
                    "moment_type": "music_trend",
                    "country": "United States",
                    "city_or_region": "online",
                    "date_start": "2026-03-01",
                    "date_end": "2026-03-01",
                    "related_artists": ["Example Artist", "Another Artist"],
                    "genre_focus": ["pop"],
                    "music_connection": "Strong music trend.",
                    "why_it_matters": "Broad reach.",
                    "playlist_angle": "Trend playlist.",
                    "suggested_playlist_title": f"Moment {index}",
                    "confidence": 0.9,
                    "sources": [
                        {
                            "title": "Official announcement",
                            "url": "https://example.com/moment",
                            "source_type": "official",
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "at most 5"):
            MomentResearchResult.from_dict({"run_date": "2026-07-07", "candidates": candidates})

    def test_rejects_blank_related_artists(self) -> None:
        with self.assertRaisesRegex(ValueError, "related_artists"):
            MomentResearchResult.from_dict(
                {
                    "run_date": "2026-07-07",
                    "candidates": [
                        {
                            "event_name": "Example Award Show Performance",
                            "event_type": "moment",
                            "moment_type": "award_performance",
                            "country": "United States",
                            "city_or_region": "Los Angeles",
                            "date_start": "2026-03-01",
                            "date_end": "2026-03-01",
                            "related_artists": [],
                            "genre_focus": ["pop"],
                            "music_connection": "Big awards sync moment.",
                            "why_it_matters": "Broad reach.",
                            "playlist_angle": "Performance songs playlist.",
                            "suggested_playlist_title": "Awards 2026",
                            "confidence": 0.9,
                            "sources": [
                                {
                                    "title": "Official announcement",
                                    "url": "https://example.com/awards",
                                    "source_type": "official",
                                }
                            ],
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
