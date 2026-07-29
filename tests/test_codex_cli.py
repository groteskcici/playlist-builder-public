from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from playlist_builder.ai.album_planner import (
    AlbumCopyJob,
    generate_and_save_album_copy,
    load_applied_plan,
)
from playlist_builder.ai.codex_cli import (
    CodexCliError,
    CodexCliRunner,
    build_album_copy_prompt,
    schema_path,
    wrap_ok_output,
)
from playlist_builder.ai.queue import AiQueue, AiQueuePaths
from playlist_builder.ai.research_competition import (
    ResearchCompetitionJob,
    generate_and_persist_research_competition,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CodexCliRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            key: os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY")
        }
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("CODEX_API_KEY", None)

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_schema_files_exist(self) -> None:
        self.assertTrue(schema_path("album_copy.json").is_file())
        self.assertTrue(schema_path("album_competition.json").is_file())
        self.assertTrue(schema_path("research_competition.json").is_file())

    def test_rejects_api_key_env(self) -> None:
        os.environ["OPENAI_API_KEY"] = "sk-test"
        runner = CodexCliRunner(
            run_subprocess=lambda *args, **kwargs: FakeCompletedProcess(0)
        )
        with self.assertRaisesRegex(CodexCliError, "OPENAI_API_KEY"):
            runner.ensure_ready()

    def test_run_json_writes_and_parses_output(self) -> None:
        schema = schema_path("album_copy.json")
        payload = {"description": "Hello playlist", "rationale": "clear"}

        def fake_run(command, **kwargs):
            if command[1:3] == ["login", "status"]:
                return FakeCompletedProcess(0)
            # codex exec ... -o <path>
            out_flag = command.index("-o")
            out_path = Path(command[out_flag + 1])
            out_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(kwargs.get("input"), "prompt text")
            return FakeCompletedProcess(0)

        runner = CodexCliRunner(run_subprocess=fake_run)
        # Bypass which() by patching ensure_ready path via fake login only —
        # also need which to find binary. Monkeypatch shutil.which via env PATH trick:
        # instead call run_json after stubbing ensure_ready.
        runner.ensure_ready = lambda: None  # type: ignore[method-assign]
        result = runner.run_json(prompt="prompt text", schema_file=schema)
        self.assertEqual(result, payload)

    def test_wrap_ok_output(self) -> None:
        wrapped = wrap_ok_output({"description": "x", "rationale": "y"})
        self.assertEqual(wrapped["status"], "ok")
        self.assertEqual(wrapped["output"]["description"], "x")


class AlbumCodexSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.queue = AiQueue(AiQueuePaths(root=Path(self._tmpdir.name) / "ai"))
        self.queue.ensure_dirs()
        self.job = AlbumCopyJob(
            job_id="album_copy:test:artist:album",
            dedupe_key="test:artist:album",
            artist_name="Artist",
            project_title="Album",
            effective_status="pre_release_candidate",
            event_date="2026-08-01",
            title="Album - Artist",
            track_uris=tuple(f"spotify:track:{i}" for i in range(20)),
            anchor_track_names=("Single A",),
            backfill_count=19,
            created_at="2026-07-22T10:00:00+00:00",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_generate_and_save_album_copy(self) -> None:
        runner = MagicMock()
        runner.run_json.return_value = {
            "description": "Released singles from Artist's Album, plus essentials.",
            "rationale": "listener facing",
        }
        plan = generate_and_save_album_copy(self.job, self.queue, runner=runner)
        self.assertEqual(plan.source, "codex_cli")
        self.assertIn("Released singles", plan.description)
        loaded = load_applied_plan(self.queue, self.job.dedupe_key)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.description, plan.description)
        runner.run_json.assert_called_once()
        prompt = runner.run_json.call_args.kwargs["prompt"]
        self.assertIn("Album - Artist", prompt)

    def test_falls_back_on_codex_error(self) -> None:
        runner = MagicMock()
        runner.run_json.side_effect = CodexCliError("boom")
        plan = generate_and_save_album_copy(
            self.job,
            self.queue,
            runner=runner,
            fallback_on_error=True,
        )
        self.assertEqual(plan.source, "template_fallback")
        self.assertTrue(plan.description)


class ResearchCodexSyncTests(unittest.TestCase):
    def test_generate_and_persist_research_competition(self) -> None:
        job = ResearchCompetitionJob(
            job_id="research_competition:tour:x",
            dedupe_key="tour:x",
            candidate_source="tour_research",
            event_type="tour",
            artist_name="Artist",
            project_title="Tour",
            event_date="2026-09-01",
            title="Official Tour Name",
            competition_search_queries=tuple(f"query {i}" for i in range(5)),
            query_results=({"query": "query 0", "results": []},),
            created_at="2026-07-22T10:00:00+00:00",
        )
        runner = MagicMock()
        runner.run_json.return_value = {
            "competition_verdict": "publish",
            "reasoning": "weak competition",
            "confidence": 0.8,
            "relevant_queries": ["query 0"],
            "relevant_competitor_ids": [],
            "revised_title": None,
            "revised_competition_search_queries": [],
        }

        class FakeStore:
            def __init__(self) -> None:
                self.recorded = None

            def get_candidate_state(self, dedupe_key: str):
                row = MagicMock()
                row.dedupe_key = dedupe_key
                row.source = "tour_research"
                row.source_event_id = "x"
                row.event_type = "tour"
                row.artist_name = "Artist"
                row.project_title = "Tour"
                row.event_date = "2026-09-01"
                row.spotify_artist_id = "artist"
                row.effective_status = "research_candidate"
                row.artist_value_tier = "high"
                row.prerelease_uri = None
                row.resolved_track_uris = []
                row.score_action = "ready_to_publish"
                row.next_check_at = None
                row.publish_pending = True
                return row

            def get_latest_payload(self, dedupe_key: str):
                return {"event": {"raw_payload": {"suggested_playlist_title": "Official Tour Name"}}}

            def record_validation(self, record):
                self.recorded = record

        store = FakeStore()
        applied = generate_and_persist_research_competition(job, store, runner=runner)
        self.assertEqual(applied.competition_verdict, "publish")
        self.assertEqual(applied.source, "codex_cli")
        self.assertIsNotNone(store.recorded)
        self.assertEqual(
            store.recorded.payload["competition_evaluation"]["competition_verdict"],
            "publish",
        )


class PromptBuilderTests(unittest.TestCase):
    def test_album_prompt_includes_instructions(self) -> None:
        prompt = build_album_copy_prompt(
            {
                "instructions": "Write description only.",
                "context": {"title": "Album - Artist", "track_uris": ["spotify:track:1"]},
            }
        )
        self.assertIn("Write description only.", prompt)
        self.assertIn("Album - Artist", prompt)


if __name__ == "__main__":
    unittest.main()
