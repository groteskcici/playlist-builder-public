from datetime import datetime, timezone
from pathlib import Path
import io
import tempfile
import unittest
from typing import Any, Mapping
from unittest.mock import patch

from PIL import Image

from playlist_builder.ai.queue import AiQueue, AiQueuePaths
from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.setlist_fm.models import ParsedSetlist, ParsedSetlistSong
from playlist_builder.spotify_publisher import SpotifyPublisher
from tests.fake_publish_pool import FakePublishPool


def _tiny_jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 160, 80)).save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


class FakePublishStore:
    def __init__(self, candidates: list[CandidateStateRow], payloads: dict[str, dict]) -> None:
        self.candidates = candidates
        self.payloads = payloads
        self.published: list[dict[str, Any]] = []

    def list_candidates(
        self,
        *,
        limit: int = 50,
        publish_pending_only: bool = False,
    ) -> list[CandidateStateRow]:
        rows = self.candidates
        if publish_pending_only:
            rows = [row for row in rows if row.publish_pending]
        return rows[:limit]

    def get_latest_payload(self, dedupe_key: str) -> dict[str, Any] | None:
        return self.payloads.get(dedupe_key)

    def mark_published(
        self,
        dedupe_key: str,
        *,
        playlist_id: str,
        playlist_uri: str,
        published_at: datetime,
        publish_slot: str | None = None,
    ) -> None:
        self.published.append(
            {
                "dedupe_key": dedupe_key,
                "playlist_id": playlist_id,
                "playlist_uri": playlist_uri,
                "published_at": published_at,
                "publish_slot": publish_slot,
            }
        )


class FakeSetlistLookup:
    def __init__(self, setlist: ParsedSetlist | None) -> None:
        self.setlist = setlist

    def find_canonical_tour_setlist(
        self,
        *,
        artist_name: str,
        tour_name: str,
        tour_year: int,
        artist_mbid: str | None = None,
        event_date=None,
    ):
        if self.setlist is None:
            return None

        class _Result:
            def __init__(self, setlist: ParsedSetlist) -> None:
                self.setlist = setlist

        return _Result(self.setlist)


class FakePlaylistClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.replaced: list[list[str]] = []
        self.covers: list[dict[str, Any]] = []

    def require_user_auth(self) -> None:
        return None

    def get_current_user(self) -> Mapping[str, Any]:
        return {"id": "user_1"}

    def create_playlist(
        self,
        *,
        user_id: str,
        name: str,
        description: str,
        public: bool = True,
    ) -> Mapping[str, Any]:
        self.created.append(
            {
                "user_id": user_id,
                "name": name,
                "description": description,
                "public": public,
            }
        )
        return {"id": "playlist_new", "uri": "spotify:playlist:playlist_new"}

    def update_playlist_details(
        self,
        playlist_id: str,
        *,
        name: str,
        description: str,
        public: bool = True,
    ) -> None:
        self.updated.append(
            {
                "playlist_id": playlist_id,
                "name": name,
                "description": description,
                "public": public,
            }
        )

    def replace_playlist_tracks(
        self,
        playlist_id: str,
        track_uris: list[str],
    ) -> None:
        self.replaced.append(track_uris)

    def upload_playlist_cover(self, playlist_id: str, jpeg_bytes: bytes) -> None:
        self.covers.append({"playlist_id": playlist_id, "bytes": jpeg_bytes})

    def get_tracks(self, track_ids: list[str]) -> Mapping[str, Mapping[str, Any]]:
        tracks: dict[str, Mapping[str, Any]] = {}
        for track_id in track_ids:
            tracks[track_id] = {
                "id": track_id,
                "album": {
                    "images": [
                        {"height": 640, "width": 640, "url": f"https://example.test/{track_id}.jpg"},
                    ]
                },
            }
        return tracks

    def get_album_track_uris(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[str]:
        return ["spotify:track:album_1", "spotify:track:album_2"]

    def search_artists(
        self,
        query: str,
        *,
        limit: int = 5,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        normalized = query.strip().lower()
        return [
            {
                "id": f"artist_{normalized.replace(' ', '_')}",
                "name": query.strip(),
                "popularity": 80,
            }
        ]

    def get_artists(self, artist_ids: list[str]) -> Mapping[str, Mapping[str, Any]]:
        artists: dict[str, Mapping[str, Any]] = {}
        for artist_id in artist_ids:
            artists[artist_id] = {
                "id": artist_id,
                "images": [
                    {
                        "url": f"https://example.test/{artist_id}.jpg",
                        "height": 640,
                        "width": 640,
                    }
                ],
            }
        return artists

    def get_artist_top_tracks(
        self,
        artist_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        suffix = artist_id.rsplit("_", maxsplit=1)[-1]
        return [
            {"uri": f"spotify:track:{suffix}_{index}"}
            for index in range(1, 11)
        ]

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        market: str | None = None,
        limit: int = 30,
    ) -> list[Mapping[str, Any]]:
        suffix = artist_id.rsplit("_", maxsplit=1)[-1]
        return [
            {"id": f"album_{suffix}_{index}"}
            for index in range(1, min(limit, 3) + 1)
        ]

    def get_album_tracks(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        return [
            {"uri": f"spotify:track:{album_id}_{index}"}
            for index in range(1, 6)
        ]

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]:
        import re

        track_match = re.search(r'track:"([^"]+)"', query)
        artist_match = re.search(r'artist:"([^"]+)"', query)
        title = (track_match.group(1) if track_match else query).strip()
        artist = (artist_match.group(1) if artist_match else "Unknown Artist").strip()
        track_id = "_".join(title.lower().split())
        artist_id = "_".join(artist.lower().split())
        return [{
            "id": track_id,
            "name": title,
            "uri": f"spotify:track:setlist_{track_id}",
            "artists": [{"name": artist, "id": artist_id}],
        }]

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]:
        return {"id": playlist_id, "tracks": {"total": 20}}


def sample_candidate(**overrides) -> CandidateStateRow:
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
        "score_action": "ready_to_publish",
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


class SpotifyPublisherTests(unittest.TestCase):
    def test_publishes_ready_candidate_and_marks_store(self) -> None:
        candidate = sample_candidate()
        payload = {
            "prerelease_enrichment": {
                "resolution": {
                    "tracks": [
                        {"spotify_track": {"uri": "spotify:track:a"}},
                        {"spotify_track": {"uri": "spotify:track:b"}},
                    ]
                }
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "created")
        self.assertEqual(len(client.created), 1)
        self.assertEqual(client.replaced[0], ["spotify:track:a", "spotify:track:b"])
        self.assertEqual(len(store.published), 1)

    def test_publishes_research_candidate_without_review_gate(self) -> None:
        candidate = sample_candidate(
            dedupe_key="festival_research:festival:2026-07-08:mad-cool-festival-2026",
            source="festival_research",
            source_event_id="2026-07-08:mad-cool-festival-2026",
            event_type="festival",
            artist_name="Foo Fighters",
            project_title="Mad Cool Festival 2026",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["Foo Fighters", "Lorde"],
                "raw_payload": {
                    "primary_artists": ["Foo Fighters", "Lorde"],
                    "notable_supporting_artists": ["JENNIE"],
                    "suggested_playlist_title": "Mad Cool 2026",
                    "playlist_angle": "Festival lineup playlist.",
                    "why_it_matters": "Huge summer festival.",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
            require_ai_plan=True,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].plan.name, "Mad Cool 2026")
        self.assertEqual(results[0].track_count, 15)
        self.assertEqual(len(client.created), 1)
        self.assertEqual(len(store.published), 1)

    def test_research_candidate_backfills_to_minimum_tracks_when_top_tracks_overlap(self) -> None:
        class OverlapPlaylistClient(FakePlaylistClient):
            def get_artist_top_tracks(
                self,
                artist_id: str,
                *,
                market: str | None = None,
            ) -> list[Mapping[str, Any]]:
                suffix = artist_id.rsplit("_", maxsplit=1)[-1]
                return [
                    {"uri": f"spotify:track:shared_{index}"}
                    for index in range(1, 4)
                ] + [
                    {"uri": f"spotify:track:{suffix}_{index}"}
                    for index in range(4, 11)
                ]

        candidate = sample_candidate(
            dedupe_key="moment_research:moment:2026-07-08:kpop-demon-hunters",
            source="moment_research",
            source_event_id="2026-07-08:kpop-demon-hunters",
            event_type="moment",
            artist_name="EJAE",
            project_title="KPop Demon Hunters Hits",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["EJAE", "Audrey Nuna", "REI AMI", "HUNTR/X"],
                "raw_payload": {
                    "suggested_playlist_title": "KPop Demon Hunters Hits",
                    "playlist_angle": "Soundtrack breakout playlist.",
                    "why_it_matters": "The soundtrack moment is breaking out.",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = OverlapPlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
            require_ai_plan=True,
        )

        results = publisher.publish_pending(store, auto_only=True)
        tracks = list(results[0].plan.track_uris)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(tracks), 15)
        self.assertEqual(len(set(tracks)), 15)
        self.assertEqual(tracks[:3], [
            "spotify:track:shared_1",
            "spotify:track:shared_2",
            "spotify:track:shared_3",
        ])
        self.assertTrue(any("spotify:track:ejae_4" == track for track in tracks))
        self.assertTrue(any("spotify:track:huntr/x_4" == track for track in tracks))

    def test_tour_research_candidate_backfills_headliner_to_minimum_tracks(self) -> None:
        candidate = sample_candidate(
            dedupe_key="tour_research:tour:2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            source="tour_research",
            source_event_id="2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            event_type="tour",
            artist_name="The Weeknd",
            project_title="The Weeknd stadium run",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["The Weeknd"],
                "raw_payload": {
                    "headliner": "The Weeknd",
                    "supporting_artists": [],
                    "tour_name": "After Hours Til Dawn Tour",
                    "suggested_playlist_title": "The Weeknd Live 2026",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].track_count, 15)
        self.assertEqual(len(client.replaced[0]), 15)

    def test_tour_research_candidate_uploads_beam_cover(self) -> None:
        candidate = sample_candidate(
            dedupe_key="tour_research:tour:2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            source="tour_research",
            source_event_id="2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            event_type="tour",
            artist_name="The Weeknd",
            project_title="The Weeknd stadium run",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["The Weeknd"],
                "raw_payload": {
                    "headliner": "The Weeknd",
                    "supporting_artists": [],
                    "tour_name": "After Hours Til Dawn Tour",
                    "suggested_playlist_title": "After Hours Til Dawn Tour",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        with patch(
            "playlist_builder.spotify_publisher.download_image_bytes",
            return_value=_tiny_jpeg_bytes(),
        ):
            results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(client.covers), 1)
        self.assertEqual(client.covers[0]["playlist_id"], "playlist_new")
        self.assertTrue(client.covers[0]["bytes"].startswith(b"\xff\xd8"))

    def test_festival_research_candidate_does_not_upload_beam_cover(self) -> None:
        candidate = sample_candidate(
            dedupe_key="festival_research:festival:2026-07-08:mad-cool-festival-2026",
            source="festival_research",
            source_event_id="2026-07-08:mad-cool-festival-2026",
            event_type="festival",
            artist_name="Foo Fighters",
            project_title="Mad Cool Festival 2026",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["Foo Fighters", "Lorde"],
                "raw_payload": {
                    "primary_artists": ["Foo Fighters", "Lorde"],
                    "suggested_playlist_title": "Mad Cool 2026",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        with patch(
            "playlist_builder.spotify_publisher.download_image_bytes",
            return_value=_tiny_jpeg_bytes(),
        ):
            results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(client.covers, [])

    def test_tour_research_candidate_prioritizes_headliner_over_support(self) -> None:
        candidate = sample_candidate(
            dedupe_key="tour_research:tour:2026-07-11:iron-maiden:run-for-your-lives-world-tour-2026",
            source="tour_research",
            source_event_id="2026-07-11:iron-maiden:run-for-your-lives-world-tour-2026",
            event_type="tour",
            artist_name="Iron Maiden",
            project_title="Run For Your Lives World Tour 2026",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["Iron Maiden", "Megadeth", "Anthrax"],
                "raw_payload": {
                    "headliner": "Iron Maiden",
                    "supporting_artists": ["Megadeth", "Anthrax"],
                    "tour_name": "Run For Your Lives World Tour 2026",
                    "suggested_playlist_title": "Iron Maiden Run For Your Lives",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)
        tracks = list(results[0].plan.track_uris)
        headliner_tracks = [track for track in tracks if ":maiden_" in track or ":album_maiden_" in track]
        support_tracks = [track for track in tracks if ":megadeth_" in track or ":anthrax_" in track]

        self.assertGreater(len(headliner_tracks), len(support_tracks))
        self.assertEqual(len(tracks), 15)

    def test_tour_research_candidate_uses_setlist_when_available(self) -> None:
        candidate = sample_candidate(
            dedupe_key="tour_research:tour:2026-07-11:iron-maiden:run-for-your-lives-world-tour-2026",
            source="tour_research",
            source_event_id="2026-07-11:iron-maiden:run-for-your-lives-world-tour-2026",
            event_type="tour",
            artist_name="Iron Maiden",
            project_title="Run For Your Lives World Tour 2026",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["Iron Maiden", "Megadeth"],
                "raw_payload": {
                    "headliner": "Iron Maiden",
                    "supporting_artists": ["Megadeth"],
                    "tour_name": "Run For Your Lives World Tour 2026",
                    "suggested_playlist_title": "Iron Maiden Run For Your Lives",
                },
            }
        }
        setlist = ParsedSetlist(
            setlist_id="setlist_1",
            version_id=None,
            artist_name="Iron Maiden",
            artist_mbid=None,
            tour_name="Run For Your Lives World Tour 2026",
            venue_name="Arena",
            venue_id=None,
            city_name="Berlin",
            country_code="DE",
            event_date=datetime(2026, 7, 11, tzinfo=timezone.utc).date(),
            url=None,
            songs=tuple(
                ParsedSetlistSong(name=f"Song {index}")
                for index in range(1, 10)
            ),
        )
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
            setlist_lookup=FakeSetlistLookup(setlist),
        )

        results = publisher.publish_pending(store, auto_only=True)
        tracks = list(results[0].plan.track_uris)

        self.assertEqual(len(results), 1)
        self.assertGreaterEqual(len(tracks), 9)
        self.assertTrue(all(track.startswith("spotify:track:setlist_") for track in tracks[:9]))

    def test_tour_research_candidate_blends_short_setlist_with_fallback_fill(self) -> None:
        candidate = sample_candidate(
            dedupe_key="tour_research:tour:2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            source="tour_research",
            source_event_id="2026-07-08:the-weeknd:after-hours-til-dawn-tour",
            event_type="tour",
            artist_name="The Weeknd",
            project_title="After Hours Til Dawn Tour",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["The Weeknd"],
                "raw_payload": {
                    "headliner": "The Weeknd",
                    "supporting_artists": [],
                    "tour_name": "After Hours Til Dawn Tour",
                    "suggested_playlist_title": "The Weeknd Live 2026",
                },
            }
        }
        setlist = ParsedSetlist(
            setlist_id="setlist_2",
            version_id=None,
            artist_name="The Weeknd",
            artist_mbid=None,
            tour_name="After Hours Til Dawn Tour",
            venue_name="Stadium",
            venue_id=None,
            city_name="Paris",
            country_code="FR",
            event_date=datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
            url=None,
            songs=tuple(ParsedSetlistSong(name=f"Set Song {index}") for index in range(1, 5)),
        )
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
            setlist_lookup=FakeSetlistLookup(setlist),
        )

        results = publisher.publish_pending(store, auto_only=True)
        tracks = list(results[0].plan.track_uris)

        self.assertEqual(len(tracks), 15)
        self.assertTrue(all(track.startswith("spotify:track:setlist_") for track in tracks[:4]))
        self.assertTrue(any(":weeknd_" in track or ":album_weeknd_" in track for track in tracks[4:]))

    def test_skips_review_candidates_when_auto_only(self) -> None:
        candidate = sample_candidate(score_action="queue_for_review")
        store = FakePublishStore([candidate], {candidate.dedupe_key: {}})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(results, [])
        self.assertEqual(client.created, [])

    def test_updates_existing_playlist(self) -> None:
        candidate = sample_candidate(
            spotify_playlist_id="playlist_existing",
            spotify_playlist_uri="spotify:playlist:playlist_existing",
        )
        payload = {
            "prerelease_enrichment": {
                "resolution": {
                    "tracks": [{"spotify_track": {"uri": "spotify:track:a"}}]
                }
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(results[0].action, "updated")
        self.assertEqual(client.created, [])
        self.assertEqual(len(client.updated), 1)

    def test_refreshes_underfilled_existing_research_playlist(self) -> None:
        class UnderfilledPlaylistClient(FakePlaylistClient):
            def get_playlist(self, playlist_id: str) -> Mapping[str, Any]:
                return {"id": playlist_id, "tracks": {"total": 3}}

        candidate = sample_candidate(
            source="moment_research",
            source_event_id="2026-07-08:kpop-demon-hunters",
            event_type="moment",
            artist_name="EJAE",
            project_title="KPop Demon Hunters Hits",
            effective_status="research_candidate",
            score_action="ready_to_publish",
            publish_pending=False,
            spotify_playlist_id="playlist_existing",
            spotify_playlist_uri="spotify:playlist:playlist_existing",
            resolved_track_count=0,
            resolved_track_uris=[],
            track_uri_hash="",
            prerelease_uri=None,
        )
        payload = {
            "event": {
                "artist_names": ["EJAE", "Audrey Nuna", "REI AMI", "HUNTR/X"],
                "raw_payload": {
                    "suggested_playlist_title": "KPop Demon Hunters Hits",
                    "playlist_angle": "Soundtrack breakout playlist.",
                    "why_it_matters": "The soundtrack moment is breaking out.",
                },
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = UnderfilledPlaylistClient()
        publisher = SpotifyPublisher(
            FakePublishPool(client),
            api_client=client,
        )

        results = publisher.publish_pending(store, auto_only=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "updated")
        self.assertEqual(len(client.updated), 1)
        self.assertEqual(len(client.replaced), 1)
        self.assertEqual(len(client.replaced[0]), 15)
        self.assertEqual(len(store.published), 1)

    def test_skips_candidate_when_ai_plan_missing_in_review_mode(self) -> None:
        candidate = sample_candidate(
            effective_status="album_confirmed",
            score_action="queue_for_review",
        )
        payload = {
            "match": {
                "spotify_album": {
                    "id": "album_1",
                    "total_tracks": 2,
                }
            }
        }
        store = FakePublishStore([candidate], {candidate.dedupe_key: payload})
        client = FakePlaylistClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = AiQueue(AiQueuePaths(root=Path(tmpdir)))
            queue.ensure_dirs()
            publisher = SpotifyPublisher(
                FakePublishPool(client),
                api_client=client,
                require_ai_plan=True,
                ai_queue=queue,
            )
            results = publisher.publish_pending(store, auto_only=False)

            self.assertEqual(results, [])
            self.assertEqual(client.created, [])
            self.assertEqual(store.published, [])


if __name__ == "__main__":
    unittest.main()
