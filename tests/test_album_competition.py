"""Tests for album competition Codex wrapper (mocked)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from playlist_builder.ai.album_competition import (
    apply_album_competition_result,
    generate_album_competition_with_retitle,
    starter_album_competition_queries,
)
from playlist_builder.ai.schemas import JOB_TYPE_ALBUM_COMPETITION, parse_album_competition_result
from playlist_builder.persistence.models import CandidateStateRow


class AlbumCompetitionParseTests(unittest.TestCase):
    def test_starter_queries_are_exactly_five(self) -> None:
        queries = starter_album_competition_queries(
            "Billie Eilish", "HIT ME HARD AND SOFT", "2026-08-01"
        )
        self.assertEqual(len(queries), 5)
        self.assertTrue(all(queries))

    def test_parse_album_competition_result(self) -> None:
        parsed = parse_album_competition_result(
            {
                "status": "ok",
                "output": {
                    "title": "HIT ME HARD AND SOFT – Billie Eilish",
                    "competition_search_queries": ["a", "b", "c", "d", "e"],
                    "competition_verdict": "publish",
                    "reasoning": "Clear lane",
                    "confidence": 0.8,
                    "description": "Singles plus essentials.",
                    "rationale": "SEO title",
                    "relevant_queries": ["a"],
                    "relevant_competitor_ids": [],
                    "revised_title": None,
                    "revised_competition_search_queries": [],
                },
            }
        )
        self.assertEqual(parsed["competition_verdict"], "publish")
        self.assertEqual(parsed["title"], "HIT ME HARD AND SOFT – Billie Eilish")
        self.assertEqual(len(parsed["competition_search_queries"]), 5)

    def test_apply_result_builds_evaluation(self) -> None:
        job = {
            "id": "album_competition:x",
            "type": JOB_TYPE_ALBUM_COMPETITION,
            "context": {"dedupe_key": "x"},
        }
        applied = apply_album_competition_result(
            job,
            {
                "status": "ok",
                "output": {
                    "title": "Title",
                    "competition_search_queries": ["a", "b", "c", "d", "e"],
                    "competition_verdict": "skip",
                    "reasoning": "Crowded",
                    "confidence": 0.5,
                    "description": "desc",
                    "rationale": "r",
                    "relevant_queries": [],
                    "relevant_competitor_ids": ["p1"],
                    "revised_title": None,
                    "revised_competition_search_queries": [],
                },
            },
            track_uris=("spotify:track:1",),
        )
        self.assertEqual(applied.competition_verdict, "skip")
        self.assertEqual(applied.dedupe_key, "x")


class AlbumCompetitionRetitleFlowTests(unittest.TestCase):
    def test_generate_with_retitle_calls_codex_twice(self) -> None:
        candidate = MagicMock(spec=CandidateStateRow)
        candidate.dedupe_key = "album:1"
        candidate.artist_name = "Artist"
        candidate.project_title = "Album"
        candidate.effective_status = "pre_release_candidate"
        candidate.event_date = "2026-09-01"
        candidate.spotify_artist_id = "artist1"
        candidate.source = "genius"
        candidate.source_event_id = "1"
        candidate.event_type = "album"
        candidate.artist_value_tier = "high"
        candidate.prerelease_uri = None
        candidate.resolved_track_uris = ()
        candidate.score_action = "ready_to_publish"
        candidate.publish_pending = True
        candidate.next_check_at = None

        store = MagicMock()
        store.get_candidate_state.return_value = candidate
        store.get_latest_payload.return_value = {
            "resolved_tracks": [{"uri": "spotify:track:1", "name": "Single"}]
        }

        client = MagicMock()
        client.search_playlists.return_value = []
        client.get_playlist.return_value = {}

        catalog = MagicMock()
        runner = MagicMock()
        runner.run_json.side_effect = [
            {
                "title": "Old Title",
                "competition_search_queries": ["q1", "q2", "q3", "q4", "q5"],
                "competition_verdict": "retitle",
                "reasoning": "Need better angle",
                "confidence": 0.6,
                "description": "desc",
                "rationale": "r",
                "relevant_queries": [],
                "relevant_competitor_ids": [],
                "revised_title": "New Title",
                "revised_competition_search_queries": ["a", "b", "c", "d", "e"],
            },
            {
                "title": "New Title",
                "competition_search_queries": ["a", "b", "c", "d", "e"],
                "competition_verdict": "publish",
                "reasoning": "Good",
                "confidence": 0.9,
                "description": "Final desc",
                "rationale": "ok",
                "relevant_queries": [],
                "relevant_competitor_ids": [],
                "revised_title": None,
                "revised_competition_search_queries": [],
            },
        ]
        queue = MagicMock()

        with patch("playlist_builder.ai.album_competition.build_album_copy_jobs") as build_jobs:
            job = MagicMock()
            job.track_uris = ("spotify:track:1", "spotify:track:2")
            build_jobs.return_value = job
            applied = generate_album_competition_with_retitle(
                candidate,
                {},
                store,
                client=client,
                catalog_client=catalog,
                runner=runner,
                queue=queue,
                sleep_fn=lambda _s: None,
                stretch_seconds=0,
            )

        self.assertEqual(applied.competition_verdict, "publish")
        self.assertEqual(applied.title, "New Title")
        self.assertEqual(runner.run_json.call_count, 2)


if __name__ == "__main__":
    unittest.main()
