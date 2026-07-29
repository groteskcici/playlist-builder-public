from datetime import datetime, timezone
import unittest

from playlist_builder.watchers import WatcherContext


class WatcherTests(unittest.TestCase):
    def test_context_rejects_naive_datetimes_and_invalid_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_started_at"):
            WatcherContext(run_started_at=datetime(2026, 6, 16, 9, 0))

        with self.assertRaisesRegex(ValueError, "since"):
            WatcherContext(since=datetime(2026, 6, 16, 9, 0))

        with self.assertRaisesRegex(ValueError, "limit"):
            WatcherContext(limit=0)

        ok = WatcherContext(
            run_started_at=datetime(2026, 6, 16, 7, 0, tzinfo=timezone.utc),
            limit=5,
        )
        self.assertEqual(ok.limit, 5)


if __name__ == "__main__":
    unittest.main()
