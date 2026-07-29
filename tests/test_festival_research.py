import unittest
from datetime import date

from playlist_builder.events import EventType
from playlist_builder.festival_research import FestivalResearchResult


class FestivalResearchTests(unittest.TestCase):
    def test_parses_agent_result_and_converts_to_event(self) -> None:
        result = FestivalResearchResult.from_dict(
            {
                "run_date": "2026-07-07",
                "candidates": [
                    {
                        "event_name": "Mad Cool Festival 2026",
                        "event_type": "festival",
                        "country": "Spain",
                        "city_or_region": "Madrid",
                        "date_start": "2026-07-08",
                        "date_end": "2026-07-11",
                        "primary_artists": ["Foo Fighters", "Lorde"],
                        "notable_supporting_artists": ["JENNIE", "Moby"],
                        "genre_focus": ["alternative rock", "pop"],
                        "why_it_matters": "Anniversary lineup with major headliners.",
                        "playlist_angle": "Madrid festival playlist.",
                        "suggested_playlist_title": "Mad Cool 2026",
                        "confidence": 0.97,
                        "sources": [
                            {
                                "title": "Mad Cool lineup",
                                "url": "https://madcoolfestival.es/noticia/2026-line-up",
                                "source_type": "official",
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(result.run_date, date(2026, 7, 7))
        event = result.to_events()[0]
        self.assertEqual(event.event_type, EventType.FESTIVAL)
        self.assertEqual(event.title, "Mad Cool Festival 2026")
        self.assertEqual(event.artist_names[:2], ("Foo Fighters", "Lorde"))
        self.assertEqual(event.source_event_id, "2026-07-08:mad-cool-festival-2026")

    def test_rejects_more_than_daily_cap(self) -> None:
        candidates = []
        for index in range(11):
            candidates.append(
                {
                    "event_name": f"Festival {index}",
                    "event_type": "festival",
                    "country": "Spain",
                    "city_or_region": "Madrid",
                    "date_start": "2026-07-08",
                    "date_end": "2026-07-11",
                    "primary_artists": ["Foo Fighters", "Lorde"],
                    "notable_supporting_artists": ["JENNIE"],
                    "genre_focus": ["alternative rock", "pop"],
                    "why_it_matters": "Big lineup.",
                    "playlist_angle": "Festival playlist.",
                    "suggested_playlist_title": f"Festival {index}",
                    "confidence": 0.97,
                    "sources": [
                        {
                            "title": "Festival lineup",
                            "url": "https://example.com/festival",
                            "source_type": "official",
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "at most 10"):
            FestivalResearchResult.from_dict({"run_date": "2026-07-07", "candidates": candidates})

    def test_rejects_candidates_without_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "source"):
            FestivalResearchResult.from_dict(
                {
                    "run_date": "2026-07-07",
                    "candidates": [
                        {
                            "event_name": "Mad Cool Festival 2026",
                            "event_type": "festival",
                            "country": "Spain",
                            "city_or_region": "Madrid",
                            "date_start": "2026-07-08",
                            "date_end": "2026-07-11",
                            "primary_artists": ["Foo Fighters"],
                            "notable_supporting_artists": ["JENNIE"],
                            "genre_focus": ["alternative rock"],
                            "why_it_matters": "Anniversary lineup.",
                            "playlist_angle": "Madrid festival playlist.",
                            "suggested_playlist_title": "Mad Cool 2026",
                            "confidence": 0.97,
                            "sources": [],
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
