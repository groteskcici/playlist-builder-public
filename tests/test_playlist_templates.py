import unittest

from playlist_builder.ai.album_planner import AppliedAlbumPlan
from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.playlist_templates import (
    PlaylistPlan,
    build_playlist_plan,
    build_public_research_description,
)


def sample_candidate(**overrides) -> CandidateStateRow:
    from datetime import datetime, timezone

    base = {
        "dedupe_key": "genius:2026-07-03:Madonna:CONFESSIONS II",
        "source": "genius",
        "source_event_id": "2026-07-03:Madonna:CONFESSIONS II",
        "event_type": "upcoming_album_candidate",
        "artist_name": "Madonna",
        "project_title": "CONFESSIONS II",
        "event_date": "2026-07-03",
        "spotify_artist_id": "artist_1",
        "effective_status": "pre_release_candidate",
        "artist_value_tier": "high",
        "prerelease_uri": "spotify:prerelease:abc",
        "resolved_track_count": 2,
        "resolved_track_uris": ["spotify:track:a", "spotify:track:b"],
        "track_uri_hash": "hash",
        "score_action": "queue_for_review",
        "last_checked_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
        "last_change_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
        "next_check_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
        "publish_pending": True,
        "spotify_playlist_id": None,
        "spotify_playlist_uri": None,
        "last_published_at": None,
    }
    base.update(overrides)
    return CandidateStateRow(**base)


class PlaylistTemplateTests(unittest.TestCase):
    def test_pre_release_template_uses_album_first_title(self) -> None:
        plan = build_playlist_plan(
            sample_candidate(),
            {
                "prerelease_enrichment": {
                    "resolution": {
                        "tracks": [
                            {"spotify_track": {"uri": "spotify:track:a"}},
                            {"spotify_track": {"uri": "spotify:track:b"}},
                        ]
                    }
                }
            },
        )

        self.assertIsInstance(plan, PlaylistPlan)
        self.assertEqual(plan.template, "pre_release_album")
        self.assertEqual(plan.name, "CONFESSIONS II – Madonna (full album)")
        self.assertEqual(plan.track_uris, ("spotify:track:a", "spotify:track:b"))

    def test_album_drop_template_uses_album_first_title(self) -> None:
        plan = build_playlist_plan(
            sample_candidate(
                effective_status="album_confirmed",
                score_action="ready_to_publish",
            ),
            {
                "match": {
                    "spotify_album": {
                        "id": "album_1",
                        "total_tracks": 12,
                    }
                }
            },
            album_track_uris=[
                "spotify:track:1",
                "spotify:track:2",
                "spotify:track:3",
            ],
        )

        self.assertEqual(plan.template, "album_drop")
        self.assertEqual(plan.name, "CONFESSIONS II – Madonna (full album)")
        self.assertEqual(len(plan.track_uris), 3)

    def test_applied_plan_overrides_tracks_and_copy(self) -> None:
        applied = AppliedAlbumPlan(
            job_id="album_plan:test",
            dedupe_key="test",
            title="CONFESSIONS II – Madonna",
            description="Singles first, then essentials.",
            track_uris=tuple(f"spotify:track:{index}" for index in range(18)),
            source="codex_cli",
        )
        plan = build_playlist_plan(
            sample_candidate(),
            {},
            applied_plan=applied,
        )

        self.assertEqual(plan.name, "CONFESSIONS II – Madonna (full album)")
        self.assertEqual(len(plan.track_uris), 18)
        self.assertEqual(plan.track_uris[0], "spotify:track:0")

    def test_festival_research_description_is_listener_facing(self) -> None:
        description = build_public_research_description(
            sample_candidate(
                event_type="festival",
                effective_status="research_candidate",
                artist_name="Charli XCX",
                project_title="Reading Festival 2026",
            ),
            {
                "event": {
                    "raw_payload": {
                        "event_name": "Reading Festival 2026",
                        "primary_artists": [
                            "Charli XCX",
                            "Dave",
                            "Florence + The Machine",
                            "Fontaines D.C.",
                        ],
                        "genre_focus": ["pop", "rap", "alternative"],
                    }
                }
            },
        )

        self.assertEqual(
            description,
            "Reading Festival 2026 lineup playlist with Charli XCX, Dave, Florence + The Machine, and Fontaines D.C. and more from this year's festival across pop, rap, and alternative.",
        )

    def test_tour_research_description_mentions_support(self) -> None:
        description = build_public_research_description(
            sample_candidate(
                event_type="tour",
                effective_status="research_candidate",
                artist_name="Iron Maiden",
                project_title="Run For Your Lives World Tour 2026",
            ),
            {
                "event": {
                    "raw_payload": {
                        "headliner": "Iron Maiden",
                        "tour_name": "Run For Your Lives World Tour 2026",
                        "supporting_artists": ["Megadeth", "Anthrax"],
                    }
                }
            },
        )

        self.assertEqual(
            description,
            "Iron Maiden tour playlist with essentials and live favorites, plus songs from Megadeth and Anthrax for the Run For Your Lives World Tour 2026 run.",
        )

    def test_tour_research_description_avoids_double_the(self) -> None:
        description = build_public_research_description(
            sample_candidate(
                event_type="tour",
                effective_status="research_candidate",
                artist_name="Hilary Duff",
                project_title="The Lucky Me Tour",
            ),
            {
                "event": {
                    "raw_payload": {
                        "headliner": "Hilary Duff",
                        "tour_name": "the lucky me tour",
                        "supporting_artists": ["La Roux"],
                    }
                }
            },
        )

        self.assertEqual(
            description,
            "Hilary Duff tour playlist with essentials and live favorites, plus songs from La Roux for the lucky me tour run.",
        )
        self.assertNotIn("for the the ", description)

    def test_moment_research_description_mentions_related_artists(self) -> None:
        description = build_public_research_description(
            sample_candidate(
                event_type="moment",
                effective_status="research_candidate",
                artist_name="Oasis",
                project_title="Oasis comeback",
            ),
            {
                "event": {
                    "raw_payload": {
                        "event_name": "Oasis documentary rollout",
                        "related_artists": ["Oasis", "Liam Gallagher", "Noel Gallagher"],
                    }
                }
            },
        )

        self.assertEqual(
            description,
            "Oasis essentials plus key songs from Liam Gallagher and Noel Gallagher around Oasis documentary rollout.",
        )


if __name__ == "__main__":
    unittest.main()
