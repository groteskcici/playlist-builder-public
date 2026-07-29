"""Dry-run style tests for the unified daily orchestrator."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from playlist_builder.daily_pipeline import DailyPipelineConfig, run_daily_pipeline


class DailyPipelineOrchestratorTests(unittest.TestCase):
    def test_soft_fails_album_discovery_and_skips_publish(self) -> None:
        store = MagicMock()
        store.list_candidates.return_value = []
        alerts: list[dict] = []

        scheduler = MagicMock()
        scheduler.run.side_effect = RuntimeError("GeniusPageFetchError: blocked")

        result = run_daily_pipeline(
            store,
            config=DailyPipelineConfig(
                skip_research_gen=True,
                skip_publish=True,
                stretch_seconds=0,
            ),
            scheduler=scheduler,
            sleep_fn=lambda _s: None,
            alert_fn=lambda message, level="warning", context=None: alerts.append(
                {"message": message, "level": level, "context": context or {}}
            )
            or {"logged": True},
            today=date(2026, 7, 22),
        )

        self.assertFalse(result.album_discovery_ok)
        self.assertTrue(alerts)
        self.assertIn("Album discovery failed", alerts[0]["message"])
        self.assertIsNone(result.fatal_error)

    def test_floor_alert_when_nothing_published(self) -> None:
        store = MagicMock()
        store.list_candidates.return_value = []
        alerts: list[dict] = []

        with patch(
            "playlist_builder.daily_pipeline.load_daily_publish_state"
        ) as load_state, patch(
            "playlist_builder.daily_pipeline.remaining_daily_capacity",
            return_value=20,
        ):
            state = MagicMock()
            state.published_count = 0
            load_state.return_value = state
            result = run_daily_pipeline(
                store,
                config=DailyPipelineConfig(
                    skip_discover=True,
                    skip_research_gen=True,
                    skip_publish=False,
                    stretch_seconds=0,
                    daily_floor=5,
                ),
                competition_client=MagicMock(),
                catalog_client=MagicMock(),
                publisher=MagicMock(),
                runner=MagicMock(),
                queue=MagicMock(),
                sleep_fn=lambda _s: None,
                alert_fn=lambda message, level="warning", context=None: alerts.append(
                    {"message": message, "level": level}
                )
                or {"logged": True},
                today=date(2026, 7, 22),
            )

        self.assertTrue(any("floor missed" in a["message"] for a in alerts))
        self.assertEqual(result.published_count, 0)


if __name__ == "__main__":
    unittest.main()
