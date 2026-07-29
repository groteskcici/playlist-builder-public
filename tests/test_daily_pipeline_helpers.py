"""Tests for daily publish state and pipeline alerts."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from playlist_builder.daily_publish_state import (
    load_daily_publish_state,
    record_daily_publish,
    remaining_daily_capacity,
)
from playlist_builder.pipeline_alerts import send_pipeline_alert


class DailyPublishStateTests(unittest.TestCase):
    def test_records_and_caps_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_publish_state.json"
            with patch(
                "playlist_builder.daily_publish_state.daily_publish_state_path",
                return_value=path,
            ):
                today = date(2026, 7, 22)
                state = record_daily_publish("a", today=today)
                self.assertEqual(state.published_count, 1)
                # Duplicate key ignored
                state = record_daily_publish("a", today=today)
                self.assertEqual(state.published_count, 1)
                record_daily_publish("b", today=today)
                self.assertEqual(remaining_daily_capacity(today=today, cap=20), 18)
                loaded = load_daily_publish_state(today=today)
                self.assertEqual(loaded.published_keys, ("a", "b"))


class PipelineAlertTests(unittest.TestCase):
    def test_logs_without_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "pipeline-alerts.log"
            with patch(
                "playlist_builder.pipeline_alerts.alerts_log_path",
                return_value=log_path,
            ), patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
                clear=False,
            ):
                result = send_pipeline_alert("test alert", level="warning", context={"n": 1})
            self.assertTrue(result["logged"])
            self.assertFalse(result["telegram"]["sent"])
            line = log_path.read_text(encoding="utf-8").strip()
            payload = json.loads(line)
            self.assertEqual(payload["message"], "test alert")

    def test_telegram_send_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "pipeline-alerts.log"
            with patch(
                "playlist_builder.pipeline_alerts.alerts_log_path",
                return_value=log_path,
            ), patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"},
                clear=False,
            ), patch("playlist_builder.pipeline_alerts.urlopen") as urlopen_mock:
                response = MagicMock()
                response.read.return_value = b'{"ok": true, "result": {}}'
                response.__enter__.return_value = response
                response.__exit__.return_value = False
                urlopen_mock.return_value = response
                result = send_pipeline_alert("hi")
            self.assertTrue(result["telegram"]["sent"])


if __name__ == "__main__":
    unittest.main()
