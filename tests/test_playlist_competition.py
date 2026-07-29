import unittest
from typing import Any, Mapping

from playlist_builder.playlist_competition import (
    PlaylistCompetitionChecker,
    PlaylistCompetitionConfig,
    build_album_playlist_queries,
)


class FakePlaylistCompetitionClient:
    def __init__(
        self,
        search_results: Mapping[str, list[Mapping[str, Any]]],
        playlist_details: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._search_results = search_results
        self._playlist_details = playlist_details

    def search_playlists(self, query: str, *, limit: int = 10, market: str | None = None):
        return list(self._search_results.get(query, []))[:limit]

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]:
        return self._playlist_details[playlist_id]


class PlaylistCompetitionTests(unittest.TestCase):
    def test_builds_album_queries(self) -> None:
        self.assertEqual(
            build_album_playlist_queries("Lorde", "Virgin", "2026-06-27"),
            ("Lorde Virgin", "Virgin Lorde", "Lorde Virgin 2026"),
        )

    def test_skips_when_relevant_current_year_results_hit_threshold(self) -> None:
        client = FakePlaylistCompetitionClient(
            {
                "Lorde Virgin": [
                    {"id": f"p{i}", "name": f"Lorde Virgin 2026 playlist {i}"}
                    for i in range(1, 9)
                ],
                "Virgin Lorde": [],
                "Lorde Virgin 2026": [],
            },
            {
                f"p{i}": {"followers": {"total": i}, "owner": {"display_name": f"u{i}"}}
                for i in range(1, 9)
            },
        )
        checker = PlaylistCompetitionChecker(client)

        result = checker.evaluate(
            event_name="Lorde Virgin",
            event_date="2026-06-27",
            queries=build_album_playlist_queries("Lorde", "Virgin", "2026-06-27"),
            compare_names=("Lorde Virgin", "Virgin Lorde"),
        )

        self.assertTrue(result.should_skip)
        self.assertEqual(result.reason, "crowded_keyword")

    def test_can_exclude_own_playlist_from_backtest(self) -> None:
        client = FakePlaylistCompetitionClient(
            {
                "Lorde Virgin": [{"id": "ours", "name": "Lorde Virgin 2026"}],
                "Virgin Lorde": [],
                "Lorde Virgin 2026": [],
            },
            {
                "ours": {"followers": {"total": 9999}, "owner": {"display_name": "us"}},
            },
        )
        checker = PlaylistCompetitionChecker(client, config=PlaylistCompetitionConfig())

        result = checker.evaluate(
            event_name="Lorde Virgin",
            event_date="2026-06-27",
            exclude_playlist_ids={"ours"},
            queries=build_album_playlist_queries("Lorde", "Virgin", "2026-06-27"),
            compare_names=("Lorde Virgin", "Virgin Lorde"),
        )

        self.assertFalse(result.should_skip)
        self.assertEqual(result.relevant_count, 0)


if __name__ == "__main__":
    unittest.main()
