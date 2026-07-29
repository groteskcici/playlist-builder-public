"""Tests for research generation dedupe against existing DB candidates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from playlist_builder.ai.research_generation import (
    build_research_generation_prompt,
    generate_research_json,
)
from playlist_builder.research_dedupe import (
    ExistingResearchIndex,
    extend_research_index_from_payload,
    filter_research_payload,
    load_existing_research_index,
    normalize_research_name,
)


def _festival_candidate(event_name: str = "Outside Lands 2026") -> dict:
    return {
        "event_name": event_name,
        "event_type": "festival",
        "country": "USA",
        "city_or_region": "San Francisco",
        "date_start": "2026-08-07",
        "date_end": "2026-08-09",
        "primary_artists": ["A", "B", "C"],
        "notable_supporting_artists": ["D", "E", "F", "G"],
        "genre_focus": ["indie"],
        "why_it_matters": "Major fest with strong lineup.",
        "playlist_angle": "Lineup essentials playlist.",
        "suggested_playlist_title": event_name,
        "competition_search_queries": [
            event_name,
            "Outside Lands",
            "Outside Lands fest",
            "Outside Lands lineup",
            "Outside Lands San Francisco",
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


class ResearchDedupeHelperTests(unittest.TestCase):
    def test_normalize_strips_year_and_case(self) -> None:
        self.assertEqual(
            normalize_research_name("Outside Lands 2026"),
            normalize_research_name("outside lands"),
        )

    def test_load_index_from_store_rows(self) -> None:
        row = MagicMock()
        row.source = "festival_research"
        row.event_type = "festival"
        row.dedupe_key = "festival_research:festival:2026-08-07:outside-lands-2026"
        row.project_title = "Outside Lands 2026"
        row.source_event_id = "2026-08-07:outside-lands-2026"
        store = MagicMock()
        store.list_candidates.return_value = [row]

        index = load_existing_research_index(store)
        self.assertIn(row.dedupe_key, index.dedupe_keys)
        self.assertIn(normalize_research_name("Outside Lands 2026"), index.name_keys)

    def test_filter_drops_existing_by_name(self) -> None:
        payload = {
            "run_date": "2026-07-23",
            "candidates": [
                _festival_candidate("Outside Lands 2026"),
                _festival_candidate("Brand New Fest 2026"),
            ],
        }
        # Fix second candidate queries/title briefly
        payload["candidates"][1]["competition_search_queries"] = [
            "Brand New Fest 2026",
            "Brand New Fest",
            "Brand New Festival",
            "Brand New Fest lineup",
            "Brand New Fest 2026 lineup",
        ]
        payload["candidates"][1]["suggested_playlist_title"] = "Brand New Fest 2026"

        existing = ExistingResearchIndex(
            dedupe_keys=frozenset(),
            name_keys=frozenset({normalize_research_name("Outside Lands 2026")}),
        )
        result = filter_research_payload("festival", payload, existing)
        self.assertEqual(result.kept_count, 1)
        self.assertEqual(result.dropped_count, 1)
        self.assertEqual(result.payload["candidates"][0]["event_name"], "Brand New Fest 2026")

    def test_extend_index_from_payload(self) -> None:
        existing = ExistingResearchIndex(dedupe_keys=frozenset(), name_keys=frozenset())
        updated = extend_research_index_from_payload(
            existing,
            {"candidates": [{"event_name": "Pukkelpop 2026"}]},
        )
        self.assertIn(normalize_research_name("Pukkelpop 2026"), updated.name_keys)


class ResearchGenerationDedupeTests(unittest.TestCase):
    def test_prompt_mentions_host_dedupe(self) -> None:
        prompt = build_research_generation_prompt(
            "festival",
            run_date="2026-07-23",
            output_filename="festival_2026-07-23.json",
        )
        self.assertIn("host will dedupe", prompt)

    def test_generate_filters_against_index(self) -> None:
        payload = {
            "run_date": "2026-07-23",
            "candidates": [_festival_candidate("Outside Lands 2026")],
        }
        runner = MagicMock()
        runner.run_json.return_value = payload
        existing = ExistingResearchIndex(
            dedupe_keys=frozenset(),
            name_keys=frozenset({normalize_research_name("Outside Lands")}),
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = generate_research_json(
                "festival",
                runner=runner,
                run_date="2026-07-23",
                output_dir=Path(tmp),
                existing_index=existing,
            )
            self.assertEqual(result.candidate_count, 0)
            self.assertEqual(result.dropped_count, 1)
            saved = json.loads(result.output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["candidates"], [])


if __name__ == "__main__":
    unittest.main()
