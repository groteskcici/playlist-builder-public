from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from playlist_builder.ai.research_generation import (
    build_research_generation_prompt,
    extract_prompt_section,
    generate_research_json,
)


class ResearchGenerationPromptTests(unittest.TestCase):
    def test_extracts_festival_section(self) -> None:
        section = extract_prompt_section("festival")
        self.assertIn("Festival Research Prompt", section)
        self.assertNotIn("Tour Research Prompt", section)

    def test_build_prompt_rewrites_output_instructions(self) -> None:
        prompt = build_research_generation_prompt(
            "festival",
            run_date="2026-07-22",
            output_filename="festival_2026-07-22.json",
        )
        self.assertIn("2026-07-22", prompt)
        self.assertIn("Do not write files yourself", prompt)
        self.assertNotIn("{{RUN_DATE}}", prompt)


class ResearchGenerationRunTests(unittest.TestCase):
    def test_generate_writes_and_validates(self) -> None:
        payload = {
            "run_date": "2026-07-22",
            "candidates": [
                {
                    "event_name": "Test Fest",
                    "event_type": "festival",
                    "country": "Germany",
                    "city_or_region": "Berlin",
                    "date_start": "2026-08-01",
                    "date_end": "2026-08-03",
                    "primary_artists": ["A", "B", "C"],
                    "notable_supporting_artists": ["D", "E", "F", "G"],
                    "genre_focus": ["indie"],
                    "why_it_matters": "Major indie fest with strong lineup.",
                    "playlist_angle": "Lineup essentials playlist.",
                    "suggested_playlist_title": "Test Fest 2026",
                    "competition_search_queries": [
                        "Test Fest 2026",
                        "Test Fest lineup",
                        "Test Fest",
                        "Test Fest Berlin",
                        "Test Festival 2026",
                    ],
                    "confidence": 0.8,
                    "sources": [
                        {
                            "title": "Official",
                            "url": "https://example.com/lineup",
                            "source_type": "official",
                        }
                    ],
                }
            ],
        }
        runner = MagicMock()
        runner.run_json.return_value = payload

        with tempfile.TemporaryDirectory() as tmp:
            result = generate_research_json(
                "festival",
                runner=runner,
                run_date="2026-07-22",
                output_dir=Path(tmp),
            )
            self.assertEqual(result.candidate_count, 1)
            self.assertTrue(result.output_path.is_file())
            saved = json.loads(result.output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["run_date"], "2026-07-22")


if __name__ == "__main__":
    unittest.main()
