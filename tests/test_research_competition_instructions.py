"""Tests for research competition prompt rules."""

from __future__ import annotations

import unittest

from playlist_builder.ai.research_competition import (
    _research_competition_instructions,
    canonical_event_name_from_payload,
)


class ResearchCompetitionInstructionsTests(unittest.TestCase):
    def test_festival_instructions_keep_real_event_name(self) -> None:
        text = _research_competition_instructions("festival")
        self.assertIn("Festival/tour title rules (hard)", text)
        self.assertIn("canonical_event_name", text)
        self.assertIn("Jorja Smith at All Points East", text)
        self.assertIn("Do not retitle away from the real name", text)

    def test_tour_instructions_keep_real_tour_name(self) -> None:
        text = _research_competition_instructions("tour")
        self.assertIn("Festival/tour title rules (hard)", text)
        self.assertIn("Five Finger Death Punch & Lamb of God Tour", text)
        self.assertIn("20th Anniversary World Tour", text)
        self.assertIn("Do not retitle away from the real name", text)

    def test_moment_instructions_omit_festival_tour_block(self) -> None:
        text = _research_competition_instructions("moment")
        self.assertNotIn("Festival/tour title rules (hard)", text)
        self.assertIn("return verdict retitle", text)

    def test_canonical_event_name_prefers_tour_name(self) -> None:
        name = canonical_event_name_from_payload(
            {
                "event": {
                    "raw_payload": {
                        "tour_name": "20th Anniversary World Tour",
                        "event_name": "FFDP 2027 Europe",
                    }
                }
            }
        )
        self.assertEqual(name, "20th Anniversary World Tour")

    def test_canonical_event_name_falls_back_to_event_name(self) -> None:
        name = canonical_event_name_from_payload(
            {"event": {"raw_payload": {"event_name": "All Points East 2026"}}}
        )
        self.assertEqual(name, "All Points East 2026")


if __name__ == "__main__":
    unittest.main()
