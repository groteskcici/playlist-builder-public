import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from playlist_builder.ai.album_planner import (
    apply_album_copy_result,
    build_album_track_list,
    build_fallback_applied_plan,
    generate_and_save_album_copy,
    job_id_for_candidate,
    load_applied_plan,
    template_album_description,
)
from playlist_builder.ai.codex_cli import CodexCliError
from playlist_builder.ai.queue import AiQueue, AiQueuePaths
from playlist_builder.ai.schemas import (
    AlbumCopyJob,
    AlbumPlanConstraints,
    AnchorTrack,
    CatalogTrack,
)


def sample_copy_job() -> AlbumCopyJob:
    return AlbumCopyJob(
        job_id=job_id_for_candidate("test:madonna"),
        dedupe_key="test:madonna",
        artist_name="Madonna",
        project_title="CONFESSIONS II",
        effective_status="pre_release_candidate",
        event_date="2026-07-03",
        title="CONFESSIONS II - Madonna",
        track_uris=(
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:hit1",
            "spotify:track:hit2",
        ),
        anchor_track_names=("Single A", "Single B"),
        backfill_count=2,
        created_at="2026-06-23T12:00:00+00:00",
    )


class AlbumCodexApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.queue = AiQueue(AiQueuePaths(root=Path(self._tmpdir.name) / "ai"))
        self.queue.ensure_dirs()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_generate_and_save_keeps_python_tracks(self) -> None:
        job = sample_copy_job()
        runner = MagicMock()
        runner.run_json.return_value = {
            "description": (
                "Released singles from Madonna's upcoming album CONFESSIONS II, "
                "plus essentials from her catalog."
            ),
            "rationale": "ok",
        }
        plan = generate_and_save_album_copy(job, self.queue, runner=runner)
        loaded = load_applied_plan(self.queue, "test:madonna")
        assert loaded is not None
        self.assertEqual(list(plan.track_uris), list(job.track_uris))
        self.assertEqual(plan.title, job.title)
        self.assertIn("CONFESSIONS II", plan.description)
        self.assertEqual(loaded.description, plan.description)

    def test_generate_falls_back_on_error(self) -> None:
        job = sample_copy_job()
        runner = MagicMock()
        runner.run_json.side_effect = CodexCliError("down")
        plan = generate_and_save_album_copy(job, self.queue, runner=runner)
        self.assertEqual(plan.source, "template_fallback")


class AlbumPlannerTests(unittest.TestCase):
    def test_build_track_list_anchors_first(self) -> None:
        anchors = (
            AnchorTrack(uri="spotify:track:a", name="A"),
            AnchorTrack(uri="spotify:track:b", name="B"),
        )
        pool = tuple(
            CatalogTrack(
                uri=f"spotify:track:hit{i}",
                name=f"Hit {i}",
                album_name="Album",
                album_type="album",
                popularity=90 - i,
            )
            for i in range(5)
        )
        uris = build_album_track_list(anchors, pool, AlbumPlanConstraints(min_tracks=4, max_tracks=25))
        self.assertEqual(uris[:2], ("spotify:track:a", "spotify:track:b"))
        self.assertEqual(len(uris), 4)

    def test_default_track_floor_is_twenty(self) -> None:
        anchors = (AnchorTrack(uri="spotify:track:a", name="A"),)
        pool = tuple(
            CatalogTrack(
                uri=f"spotify:track:hit{i}",
                name=f"Hit {i}",
                album_name="Album",
                album_type="album",
                popularity=90 - i,
            )
            for i in range(30)
        )
        uris = build_album_track_list(anchors, pool)
        self.assertEqual(len(uris), 20)

    def test_copy_result_ignores_track_changes(self) -> None:
        job = sample_copy_job().to_dict()
        result = {
            "id": job["id"],
            "status": "ok",
            "output": {"description": "Listener-facing playlist description here."},
        }
        plan = apply_album_copy_result(job, result)
        self.assertEqual(plan.track_uris, tuple(job["context"]["track_uris"]))

    def test_fallback_uses_template_description(self) -> None:
        job = AlbumCopyJob(
            job_id="album_copy:test",
            dedupe_key="test",
            artist_name="Billie Eilish",
            project_title="HIT ME HARD AND SOFT",
            effective_status="pre_release_candidate",
            event_date="2026-05-16",
            title="HIT ME HARD AND SOFT - Billie Eilish",
            track_uris=("spotify:track:a", "spotify:track:hit1"),
            anchor_track_names=("LUNCH",),
            backfill_count=1,
            created_at="2026-06-23T12:00:00+00:00",
        )
        plan = build_fallback_applied_plan(job)
        self.assertEqual(
            plan.description,
            template_album_description(job.artist_name, job.project_title, job.effective_status),
        )


if __name__ == "__main__":
    unittest.main()
