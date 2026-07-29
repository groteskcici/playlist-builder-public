"""Unit tests for Genius proxy slot failover."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from playlist_builder.genius_page_fetcher import (
    GeniusPageFetchError,
    fetch_genius_page_html,
    genius_proxy_slots,
)


CALENDAR_HTML = """
<div data-lyrics-container="true">
7/3<br/>
Madonna - CONFESSIONS II
</div>
"""


class GeniusProxySlotsTests(unittest.TestCase):
    def test_reads_numbered_proxy_slots(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GENIUS_FETCH_PROXY_1": "http://p1",
                "GENIUS_FETCH_PROXY_2": "http://p2",
                "GENIUS_FETCH_PROXY_3": "",
                "GENIUS_FETCH_PROXY": "",
            },
            clear=False,
        ):
            self.assertEqual(genius_proxy_slots(), ["http://p1", "http://p2"])

    def test_legacy_proxy_becomes_slot_one(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GENIUS_FETCH_PROXY": "http://legacy",
                "GENIUS_FETCH_PROXY_1": "",
                "GENIUS_FETCH_PROXY_2": "",
                "GENIUS_FETCH_PROXY_3": "",
                "GENIUS_FETCH_PROXY_4": "",
                "GENIUS_FETCH_PROXY_5": "",
            },
            clear=False,
        ):
            self.assertEqual(genius_proxy_slots(), ["http://legacy"])

    def test_no_proxy_returns_direct_slot(self) -> None:
        env = {
            "GENIUS_FETCH_PROXY": "",
            "GENIUS_FETCH_PROXY_1": "",
            "GENIUS_FETCH_PROXY_2": "",
            "GENIUS_FETCH_PROXY_3": "",
            "GENIUS_FETCH_PROXY_4": "",
            "GENIUS_FETCH_PROXY_5": "",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(genius_proxy_slots(), [None])

    @patch.dict(
        os.environ,
        {
            "GENIUS_FETCH_SOLVE_CLOUDFLARE": "1",
            "GENIUS_FETCH_PROXY_1": "http://bad",
            "GENIUS_FETCH_PROXY_2": "http://good",
            "GENIUS_FETCH_PROXY": "",
        },
        clear=False,
    )
    @patch("playlist_builder.genius_page_fetcher._fetch_with_urllib")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_dynamic_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_stealthy_fetcher")
    def test_fails_over_to_second_proxy(
        self,
        stealthy_mock,
        dynamic_mock,
        fetcher_mock,
        urllib_mock,
    ) -> None:
        def stealthy_side_effect(*_args, **kwargs):
            proxy = kwargs.get("proxy")
            if proxy == "http://bad":
                raise RuntimeError("blocked")
            return CALENDAR_HTML

        stealthy_mock.side_effect = stealthy_side_effect
        dynamic_mock.side_effect = RuntimeError("unused")
        fetcher_mock.side_effect = RuntimeError("unused")
        urllib_mock.side_effect = RuntimeError("unused")

        html, fetcher_name = fetch_genius_page_html(
            "https://genius.com/example",
            headless=True,
            timeout_ms=1000,
        )
        self.assertEqual(html, CALENDAR_HTML)
        self.assertEqual(fetcher_name, "StealthyFetcher/proxy2")
        self.assertGreaterEqual(stealthy_mock.call_count, 2)


class GeniusPageFetcherNameTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "GENIUS_FETCH_SOLVE_CLOUDFLARE": "1",
            "GENIUS_FETCH_PROXY": "",
            "GENIUS_FETCH_PROXY_1": "",
            "GENIUS_FETCH_PROXY_2": "",
            "GENIUS_FETCH_PROXY_3": "",
            "GENIUS_FETCH_PROXY_4": "",
            "GENIUS_FETCH_PROXY_5": "",
        },
        clear=False,
    )
    @patch("playlist_builder.genius_page_fetcher._fetch_with_urllib")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_dynamic_fetcher")
    @patch("playlist_builder.genius_page_fetcher._fetch_with_stealthy_fetcher")
    def test_direct_fetcher_name_includes_label(
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


if __name__ == "__main__":
    unittest.main()
