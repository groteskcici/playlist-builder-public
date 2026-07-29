import os
import unittest
from unittest.mock import patch

from playlist_builder.genius_page_fetcher import (
    GeniusPageFetchError,
    _ensure_calendar_html,
    fetch_genius_page_html,
)


CALENDAR_HTML = """
<div data-lyrics-container="true">
7/3<br/>
Madonna - CONFESSIONS II
</div>
"""


class GeniusPageFetcherTests(unittest.TestCase):
    def test_ensure_calendar_html_requires_lyrics_container(self) -> None:
        with self.assertRaises(GeniusPageFetchError):
            _ensure_calendar_html("<html><body>blocked</body></html>")

    def test_ensure_calendar_html_detects_cloudflare_challenge(self) -> None:
        with self.assertRaisesRegex(GeniusPageFetchError, "Cloudflare"):
            _ensure_calendar_html(
                "<html><title>Just a moment...</title><div class='cf-challenge'></div></html>"
            )

    @patch.dict(os.environ, {"GENIUS_FETCH_SOLVE_CLOUDFLARE": "1"}, clear=False)
    @patch("playlist_builder.genius_page_fetcher._fetch_with_urllib")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_dynamic_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_stealthy_fetcher")
    def test_prefers_stealthy_first_when_cloudflare_solver_enabled(
        self,
        stealthy_mock,
        dynamic_mock,
        fetcher_mock,
        urllib_mock,
    ) -> None:
        stealthy_mock.return_value = CALENDAR_HTML

        html, fetcher_name = fetch_genius_page_html(
            "https://genius.com/example",
            headless=True,
            timeout_ms=1000,
        )

        self.assertEqual(html, CALENDAR_HTML)
        self.assertEqual(fetcher_name, "StealthyFetcher/direct")
        dynamic_mock.assert_not_called()
        fetcher_mock.assert_not_called()
        urllib_mock.assert_not_called()

    @patch.dict(os.environ, {"GENIUS_FETCH_SOLVE_CLOUDFLARE": "1"}, clear=False)
    @patch("playlist_builder.genius_page_fetcher._fetch_with_urllib")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_dynamic_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_stealthy_fetcher")
    def test_escalates_to_dynamic_when_stealthy_blocked(
        self,
        stealthy_mock,
        dynamic_mock,
        fetcher_mock,
        urllib_mock,
    ) -> None:
        stealthy_mock.return_value = "<html>Just a moment...</html>"
        dynamic_mock.return_value = CALENDAR_HTML

        html, fetcher_name = fetch_genius_page_html(
            "https://genius.com/example",
            headless=True,
            timeout_ms=1000,
        )

        self.assertEqual(html, CALENDAR_HTML)
        self.assertEqual(fetcher_name, "DynamicFetcher/direct")
        fetcher_mock.assert_not_called()
        urllib_mock.assert_not_called()

    @patch("playlist_builder.genius_page_fetcher._fetch_with_urllib")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_dynamic_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_stealthy_fetcher")
    def test_raises_when_all_fetchers_fail(
        self,
        stealthy_mock,
        dynamic_mock,
        fetcher_mock,
        urllib_mock,
    ) -> None:
        stealthy_mock.side_effect = RuntimeError("timeout")
        dynamic_mock.side_effect = RuntimeError("timeout")
        fetcher_mock.side_effect = RuntimeError("403")
        urllib_mock.side_effect = RuntimeError("403")

        with self.assertRaises(GeniusPageFetchError):
            fetch_genius_page_html("https://genius.com/example", timeout_ms=1000)


if __name__ == "__main__":
    unittest.main()
