import unittest
from datetime import date

from playlist_builder.events import EventType
from playlist_builder.tour_research import TourResearchResult


class TourResearchTests(unittest.TestCase):
    def test_parses_agent_result_and_converts_to_event(self) -> None:
        result = TourResearchResult.from_dict(
            {
                "run_date": "2026-07-07",
                "candidates": [
                    {
                        "event_name": "Olivia Rodrigo Berlin Arena Night",
                        "event_type": "tour",
                        "tour_name": "GUTS World Tour",
                        "headliner": "Olivia Rodrigo",
                        "country": "Germany",
                        "city_or_region": "Berlin",
                        "date_start": "2026-07-15",
                        "date_end": "2026-07-15",
                        "supporting_artists": ["The Last Dinner Party"],
                        "genre_focus": ["pop", "alt-pop"],
                        "tour_type": ["arena", "world tour"],
                        "why_it_matters": "One of the biggest European stops on the run.",
                        "playlist_angle": "Berlin stop playlist for a huge pop tour.",
                        "suggested_playlist_title": "Olivia Rodrigo Berlin 2026",
                        "confidence": 0.94,
                        "sources": [
                            {
                                "title": "Official tour dates",
                                "url": "https://www.oliviarodrigo.com/tour",
                                "source_type": "official",
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(result.run_date, date(2026, 7, 7))
        event = result.to_events()[0]
        self.assertEqual(event.event_type, EventType.TOUR)
        self.assertEqual(event.title, "Olivia Rodrigo Berlin Arena Night")
        self.assertEqual(event.artist_names, ("Olivia Rodrigo", "The Last Dinner Party"))
        self.assertEqual(
            event.source_event_id,
            "2026-07-15:olivia-rodrigo:guts-world-tour",
        )

    def test_rejects_more_than_daily_cap(self) -> None:
        candidates = []
        for index in range(6):
            candidates.append(
                {
                    "event_name": f"Example Tour Stop {index}",
                    "event_type": "tour",
                    "tour_name": f"Example Tour {index}",
                    "headliner": "Example Artist",
                    "country": "Germany",
                    "city_or_region": "Hamburg",
                    "date_start": "2026-07-15",
                    "date_end": "2026-07-15",
                    "supporting_artists": [],
                    "genre_focus": ["pop"],
                    "tour_type": ["arena"],
                    "why_it_matters": "Important show.",
                    "playlist_angle": "Tour stop playlist.",
                    "suggested_playlist_title": f"Example Tour {index}",
                    "confidence": 0.94,
                    "sources": [
                        {
                            "title": "Official tour dates",
                            "url": "https://example.com/tour",
                            "source_type": "official",
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "at most 5"):
            TourResearchResult.from_dict({"run_date": "2026-07-07", "candidates": candidates})

    def test_rejects_candidates_without_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "source"):
            TourResearchResult.from_dict(
                {
                    "run_date": "2026-07-07",
                    "candidates": [
                        {
                            "event_name": "Example Tour Stop",
                            "event_type": "tour",
                            "tour_name": "Example Tour",
                            "headliner": "Example Artist",
                            "country": "Germany",
                            "city_or_region": "Hamburg",
                            "date_start": "2026-07-15",
                            "date_end": "2026-07-15",
                            "supporting_artists": [],
                            "genre_focus": ["pop"],
                            "tour_type": ["arena"],
                            "why_it_matters": "Important show.",
                            "playlist_angle": "Tour stop playlist.",
                            "suggested_playlist_title": "Example Tour Hamburg",
                            "confidence": 0.94,
                            "sources": [],
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
