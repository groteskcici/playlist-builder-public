"""Create and update Spotify playlists for publish-pending candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Protocol

from playlist_builder.ai.album_planner import load_applied_plan
from playlist_builder.ai.queue import AiQueue
from playlist_builder.playlist_competition import (
    PlaylistCompetitionChecker,
    PlaylistCompetitionSkipError,
    build_album_playlist_queries,
)
from playlist_builder.persistence.models import CandidateStateRow
from playlist_builder.playlist_templates import (
    PlaylistPlan,
    album_id_from_payload,
    build_playlist_plan,
)
from playlist_builder.playlist_cover import (
    download_image_bytes,
    playlist_cover_jpeg_from_track_uris,
)
from playlist_builder.cover_template import (
    download_artist_image_bytes,
    render_artist_beam_cover,
)
from playlist_builder.scoring import ScoreAction
from playlist_builder.spotify_album_watcher import SpotifyAPIError
from playlist_builder.spotify_publish_pool import SpotifyPublishPool
from playlist_builder.setlist_fm import SetlistFmClient, SetlistLookup
from playlist_builder.setlist_fm.track_resolver import SetlistTrackResolver


MIN_RESEARCH_PLAYLIST_TRACKS = 15


class PublishStore(Protocol):
    def list_candidates(
        self,
        *,
        limit: int = 50,
        publish_pending_only: bool = False,
    ) -> list[CandidateStateRow]: ...

    def get_latest_payload(self, dedupe_key: str) -> dict[str, Any] | None: ...

    def mark_published(
        self,
        dedupe_key: str,
        *,
        playlist_id: str,
        playlist_uri: str,
        published_at: datetime,
        publish_slot: str | None = None,
    ) -> None: ...


class SpotifyAlbumTracksClient(Protocol):
    def get_album_track_uris(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[str]: ...

    def search_artists(
        self,
        query: str,
        *,
        limit: int = 5,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_artist_top_tracks(
        self,
        artist_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        market: str | None = None,
        limit: int = 30,
    ) -> list[Mapping[str, Any]]: ...

    def get_album_tracks(
        self,
        album_id: str,
        *,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PublishResult:
    dedupe_key: str
    action: str
    playlist_id: str | None
    playlist_uri: str | None
    track_count: int
    dry_run: bool
    plan: PlaylistPlan
    publish_slot: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "dedupe_key": self.dedupe_key,
            "action": self.action,
            "playlist_id": self.playlist_id,
            "playlist_uri": self.playlist_uri,
            "track_count": self.track_count,
            "dry_run": self.dry_run,
            "publish_slot": self.publish_slot,
            "plan": self.plan.to_dict(),
        }


class PlaylistPublishSkipError(RuntimeError):
    pass


class SpotifyPublisher:
    def __init__(
        self,
        publish_pool: SpotifyPublishPool,
        *,
        api_client: SpotifyAlbumTracksClient,
        market: str | None = None,
        ai_queue: AiQueue | None = None,
        require_ai_plan: bool = False,
        competition_checker: PlaylistCompetitionChecker | None = None,
        setlist_lookup: SetlistLookup | None = None,
    ) -> None:
        self._publish_pool = publish_pool
        self._api_client = api_client
        self._market = market
        self._ai_queue = ai_queue
        self._require_ai_plan = require_ai_plan
        self._competition_checker = competition_checker
        self._setlist_lookup = setlist_lookup

    @classmethod
    def from_env(
        cls,
        *,
        market: str | None = None,
        require_ai_plan: bool | None = None,
    ) -> "SpotifyPublisher":
        from playlist_builder.spotify_album_watcher import SpotifyWebAPIClient
        import os

        if require_ai_plan is None:
            require_ai_plan = os.environ.get("PLAYLIST_REQUIRE_AI_PLAN", "").strip() in {
                "1",
                "true",
                "yes",
            }

        api_client = SpotifyWebAPIClient.from_env()
        try:
            setlist_lookup = SetlistLookup(SetlistFmClient.from_env())
        except RuntimeError:
            setlist_lookup = None

        return cls(
            SpotifyPublishPool.from_env(),
            api_client=api_client,
            market=market,
            ai_queue=AiQueue.from_env(),
            require_ai_plan=require_ai_plan,
            competition_checker=PlaylistCompetitionChecker(api_client),
            setlist_lookup=setlist_lookup,
        )

    def publish_pending(
        self,
        store: PublishStore,
        *,
        limit: int = 10,
        auto_only: bool = True,
        dry_run: bool = False,
    ) -> list[PublishResult]:
        candidates = self._collect_publish_candidates(store, limit=limit, auto_only=auto_only)
        results: list[PublishResult] = []

        for candidate in candidates:
            payload = store.get_latest_payload(candidate.dedupe_key) or {}
            try:
                result = self.publish_candidate(candidate, payload, dry_run=dry_run)
            except (PlaylistCompetitionSkipError, PlaylistPublishSkipError):
                continue
            results.append(result)

            if not dry_run and result.playlist_id and result.playlist_uri:
                store.mark_published(
                    candidate.dedupe_key,
                    playlist_id=result.playlist_id,
                    playlist_uri=result.playlist_uri,
                    published_at=datetime.now(timezone.utc),
                    publish_slot=result.publish_slot,
                )

        return results

    def _collect_publish_candidates(
        self,
        store: PublishStore,
        *,
        limit: int,
        auto_only: bool,
    ) -> list[CandidateStateRow]:
        selected: list[CandidateStateRow] = []
        seen: set[str] = set()

        for candidate in store.list_candidates(limit=limit, publish_pending_only=True):
            if not _should_publish(candidate, auto_only=auto_only):
                continue
            selected.append(candidate)
            seen.add(candidate.dedupe_key)

        if len(selected) >= limit:
            return selected[:limit]

        scan_limit = max(limit * 10, 100)
        for candidate in store.list_candidates(limit=scan_limit, publish_pending_only=False):
            if candidate.dedupe_key in seen:
                continue
            if not self._should_refresh_existing_research_playlist(candidate):
                continue
            selected.append(candidate)
            seen.add(candidate.dedupe_key)
            if len(selected) >= limit:
                break

        return selected[:limit]

    def _should_refresh_existing_research_playlist(self, candidate: CandidateStateRow) -> bool:
        if candidate.publish_pending:
            return False
        if candidate.effective_status != "research_candidate":
            return False
        if candidate.score_action != ScoreAction.READY_TO_PUBLISH.value:
            return False

        playlist_id = str(candidate.spotify_playlist_id or "").strip()
        if not playlist_id:
            return False

        try:
            playlist = self._api_client.get_playlist(playlist_id)
        except Exception:
            return False

        return _playlist_track_total(playlist) < MIN_RESEARCH_PLAYLIST_TRACKS

    def publish_candidate(
        self,
        candidate: CandidateStateRow,
        payload: Mapping[str, Any],
        *,
        dry_run: bool = False,
    ) -> PublishResult:
        album_track_uris = None
        research_track_uris = None
        if candidate.effective_status == "album_confirmed":
            if dry_run:
                album_track_uris = _dry_run_album_track_uris(payload)
            else:
                album_id = album_id_from_payload(payload)
                if not album_id:
                    raise ValueError(
                        f"{candidate.dedupe_key} is album_confirmed but payload has no album id"
                    )
                album_track_uris = self._api_client.get_album_track_uris(
                    album_id,
                    market=self._market,
                )
        elif candidate.effective_status == "research_candidate":
            research_track_uris = self._build_research_track_uris(candidate, payload, dry_run=dry_run)

        applied_plan = None
        if candidate.effective_status in {"pre_release_candidate", "album_confirmed"}:
            applied_plan = self._load_applied_plan(candidate.dedupe_key)

        plan = build_playlist_plan(
            candidate,
            payload,
            album_track_uris=album_track_uris,
            research_track_uris=research_track_uris,
            applied_plan=applied_plan,
        )

        evaluation = payload.get("competition_evaluation")
        if isinstance(evaluation, Mapping):
            verdict = str(evaluation.get("competition_verdict") or "").strip().lower()
            if verdict != "publish":
                raise PlaylistPublishSkipError(
                    f"AI competition gate blocked {candidate.dedupe_key}: "
                    f"verdict={verdict or 'missing'}"
                )
        elif (
            self._competition_checker is not None
            and candidate.effective_status != "research_candidate"
        ):
            competition = self._competition_checker.evaluate(
                event_name=f"{candidate.artist_name} {candidate.project_title}",
                event_date=candidate.event_date,
                market=self._market,
                exclude_playlist_ids={str(candidate.spotify_playlist_id or '').strip()},
                queries=build_album_playlist_queries(
                    candidate.artist_name,
                    candidate.project_title,
                    candidate.event_date,
                ),
                compare_names=(
                    f"{candidate.artist_name} {candidate.project_title}",
                    f"{candidate.project_title} {candidate.artist_name}",
                ),
            )
            if competition.should_skip:
                raise PlaylistCompetitionSkipError(
                    f"playlist keyword crowded for {candidate.dedupe_key}: {competition.relevant_count} relevant playlists"
                )

        if dry_run:
            return PublishResult(
                dedupe_key=candidate.dedupe_key,
                action=_publish_action(candidate),
                playlist_id=candidate.spotify_playlist_id,
                playlist_uri=candidate.spotify_playlist_uri,
                track_count=len(plan.track_uris),
                dry_run=True,
                plan=plan,
                publish_slot=candidate.spotify_publish_slot,
            )

        cover_jpeg = self._album_playlist_cover_jpeg(
            candidate, plan.track_uris
        ) or self._tour_playlist_cover_jpeg(candidate, payload, plan.name)
        write_result = self._publish_pool.write_playlist(
            plan=plan,
            track_uris=list(plan.track_uris),
            existing_playlist_id=candidate.spotify_playlist_id,
            existing_slot=candidate.spotify_publish_slot,
            cover_jpeg=cover_jpeg,
        )

        return PublishResult(
            dedupe_key=candidate.dedupe_key,
            action=write_result.action,
            playlist_id=write_result.playlist_id,
            playlist_uri=write_result.playlist_uri,
            track_count=len(plan.track_uris),
            dry_run=False,
            plan=plan,
            publish_slot=write_result.publish_slot,
        )

    def _album_playlist_cover_jpeg(
        self,
        candidate: CandidateStateRow,
        track_uris: tuple[str, ...] | list[str],
    ) -> bytes | None:
        """Fetch promo/album track artwork for album playlist covers.

        Soft-fails (returns None) if artwork cannot be resolved — create/update
        still proceeds. Upload itself requires the ugc-image-upload OAuth scope.
        """

        if candidate.effective_status not in {"pre_release_candidate", "album_confirmed"}:
            return None
        if not track_uris:
            return None
        try:
            return playlist_cover_jpeg_from_track_uris(
                track_uris,
                get_tracks=self._api_client.get_tracks,
            )
        except (SpotifyAPIError, RuntimeError, TypeError, ValueError, AttributeError):
            return None

    def _tour_playlist_cover_jpeg(
        self,
        candidate: CandidateStateRow,
        payload: Mapping[str, Any],
        playlist_title: str,
    ) -> bytes | None:
        """Render dual-beam cover from the tour headliner's Spotify artist image.

        Soft-fails so create/update still proceeds without a custom cover.
        """

        if candidate.effective_status != "research_candidate":
            return None
        if str(candidate.event_type or "").strip().casefold() != "tour":
            return None
        title = " ".join(str(playlist_title or "").split()).strip()
        if not title:
            return None

        try:
            artist_names = _research_artist_names(candidate, payload)
            if not artist_names:
                return None
            artist = _resolve_artist(
                self._api_client,
                artist_names[0],
                market=self._market,
            )
            if artist is None:
                return None
            artist_id = str(artist.get("id") or "").strip()
            if not artist_id:
                return None

            artist_payload: Mapping[str, Any] = artist
            get_artists = getattr(self._api_client, "get_artists", None)
            if callable(get_artists):
                artists = get_artists([artist_id])
                if isinstance(artists, Mapping):
                    fetched = artists.get(artist_id)
                    if isinstance(fetched, Mapping):
                        artist_payload = fetched

            image_bytes = download_artist_image_bytes(
                artist_payload,
                download=download_image_bytes,
            )
            if not image_bytes:
                return None
            return render_artist_beam_cover(
                artist_image=image_bytes,
                title=title,
            )
        except (SpotifyAPIError, RuntimeError, TypeError, ValueError, OSError):
            return None

    def _build_research_track_uris(
        self,
        candidate: CandidateStateRow,
        payload: Mapping[str, Any],
        *,
        dry_run: bool = False,
    ) -> list[str]:
        artist_names = _research_artist_names(candidate, payload)
        if dry_run:
            count = 15 if candidate.event_type == "tour" else max(1, min(max(len(artist_names) * 3, 15), 20))
            return [f"spotify:track:dry_run_research_{index}" for index in range(count)]

        resolved_artists: list[tuple[str, str]] = []
        for artist_name in artist_names:
            artist = _resolve_artist(self._api_client, artist_name, market=self._market)
            if artist is None:
                continue
            artist_id = artist.get("id")
            if not isinstance(artist_id, str) or not artist_id.strip():
                continue
            resolved_artists.append((artist_name, artist_id))

        if candidate.event_type == "tour":
            track_uris = self._build_tour_research_track_uris(candidate, payload, resolved_artists)
        else:
            track_uris = self._build_balanced_research_track_uris(resolved_artists)

        if not track_uris:
            raise PlaylistPublishSkipError(
                f"no Spotify top tracks resolved for research candidate {candidate.dedupe_key}"
            )
        return track_uris

    def _build_balanced_research_track_uris(
        self,
        resolved_artists: list[tuple[str, str]],
    ) -> list[str]:
        target_tracks = MIN_RESEARCH_PLAYLIST_TRACKS
        max_tracks = 50
        artist_pools = [
            self._artist_track_pool(artist_id, include_catalog=True)
            for _, artist_id in resolved_artists
        ]

        track_uris: list[str] = []
        seen: set[str] = set()
        index = 0
        while len(track_uris) < target_tracks:
            added_this_round = False
            for available in artist_pools:
                if index >= len(available):
                    continue
                uri = available[index]
                if uri in seen:
                    continue
                track_uris.append(uri)
                seen.add(uri)
                added_this_round = True
                if len(track_uris) >= max_tracks:
                    return track_uris
                if len(track_uris) >= target_tracks:
                    return track_uris
            if not added_this_round:
                if all(index >= len(available) for available in artist_pools):
                    break
            index += 1

        return track_uris

    def _build_tour_research_track_uris(
        self,
        candidate: CandidateStateRow,
        payload: Mapping[str, Any],
        resolved_artists: list[tuple[str, str]],
    ) -> list[str]:
        if not resolved_artists:
            return []

        target_tracks = MIN_RESEARCH_PLAYLIST_TRACKS
        setlist_uris = self._tour_setlist_track_uris(candidate, payload)
        if len(setlist_uris) >= target_tracks:
            return setlist_uris
        headliner_name, headliner_id = resolved_artists[0]
        headliner_tracks = self._artist_track_pool(headliner_id, include_catalog=True)
        support_pools = [
            self._artist_track_pool(artist_id)
            for _, artist_id in resolved_artists[1:]
        ]

        track_uris: list[str] = []
        seen: set[str] = set()

        if setlist_uris:
            _append_unique(track_uris, seen, setlist_uris, limit=target_tracks)

        headliner_seed = 8 if support_pools else target_tracks
        _append_unique(track_uris, seen, headliner_tracks[:headliner_seed], limit=target_tracks)

        for support_tracks in support_pools:
            _append_unique(track_uris, seen, support_tracks[:2], limit=target_tracks)
            if len(track_uris) >= target_tracks:
                return track_uris[:50]

        if len(track_uris) < target_tracks:
            _append_unique(track_uris, seen, headliner_tracks[headliner_seed:], limit=target_tracks)

        if len(track_uris) < target_tracks:
            for support_tracks in support_pools:
                _append_unique(track_uris, seen, support_tracks[2:], limit=target_tracks)
                if len(track_uris) >= target_tracks:
                    break

        if not track_uris and candidate.artist_name.strip() == headliner_name.strip():
            return []
        return track_uris[:50]

    def _tour_setlist_track_uris(
        self,
        candidate: CandidateStateRow,
        payload: Mapping[str, Any],
    ) -> list[str]:
        if self._setlist_lookup is None:
            return []

        raw_payload = _event_raw_payload(payload)
        tour_name = _mapping_text(raw_payload, "tour_name") or candidate.project_title.strip()
        headliner = _mapping_text(raw_payload, "headliner") or candidate.artist_name.strip()
        event_date = _iso_date(candidate.event_date)
        if not headliner or not tour_name:
            return []

        try:
            lookup = self._setlist_lookup.find_canonical_tour_setlist(
                artist_name=headliner,
                tour_name=tour_name,
                tour_year=(event_date or date.today()).year,
                event_date=event_date,
            )
        except Exception:
            return []
        if lookup is None or lookup.setlist.live_song_count <= 0:
            return []

        resolver = SetlistTrackResolver(self._api_client, market=self._market)
        try:
            resolved = resolver.resolve(lookup.setlist)
        except Exception:
            return []

        return [track.uri for track in resolved.tracks if isinstance(track.uri, str) and track.uri]

    def _artist_track_pool(
        self,
        artist_id: str,
        *,
        include_catalog: bool = False,
    ) -> list[str]:
        available: list[str] = []
        seen: set[str] = set()

        for track in self._api_client.get_artist_top_tracks(artist_id, market=self._market):
            uri = track.get("uri")
            if isinstance(uri, str) and uri and uri not in seen:
                available.append(uri)
                seen.add(uri)

        if not include_catalog:
            return available

        albums = self._api_client.get_artist_albums(artist_id, market=self._market, limit=12)
        for album in albums:
            album_id = album.get("id")
            if not isinstance(album_id, str) or not album_id.strip():
                continue
            for track in self._api_client.get_album_tracks(album_id, market=self._market):
                uri = track.get("uri")
                if isinstance(uri, str) and uri and uri not in seen:
                    available.append(uri)
                    seen.add(uri)
                if len(available) >= 30:
                    return available
        return available

    def _load_applied_plan(self, dedupe_key: str):
        if self._ai_queue is None:
            if self._require_ai_plan:
                raise ValueError(
                    f"AI playlist plan required but queue is not configured for {dedupe_key}"
                )
            return None

        plan = load_applied_plan(self._ai_queue, dedupe_key)
        if plan is None and self._require_ai_plan:
            raise PlaylistPublishSkipError(
                f"AI playlist plan required but missing for {dedupe_key} "
                f"(run enqueue + OpenClaw merge first)"
            )
        return plan


def _should_publish(candidate: CandidateStateRow, *, auto_only: bool) -> bool:
    if not candidate.publish_pending:
        return False
    if candidate.score_action == ScoreAction.READY_TO_PUBLISH.value:
        return True
    if auto_only:
        return False
    return candidate.score_action == ScoreAction.QUEUE_FOR_REVIEW.value


def _publish_action(candidate: CandidateStateRow) -> str:
    if candidate.spotify_playlist_id:
        return "updated"
    return "created"


def _research_artist_names(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
) -> list[str]:
    event = payload.get("event")
    raw_payload = event.get("raw_payload") if isinstance(event, Mapping) else None

    names: list[str] = []
    if isinstance(raw_payload, Mapping):
        if candidate.event_type == "festival":
            names.extend(_string_list(raw_payload.get("primary_artists")))
            names.extend(_string_list(raw_payload.get("notable_supporting_artists"))[:5])
        elif candidate.event_type == "tour":
            headliner = raw_payload.get("headliner")
            if isinstance(headliner, str) and headliner.strip():
                names.append(headliner.strip())
            names.extend(_string_list(raw_payload.get("supporting_artists"))[:4])
        elif candidate.event_type == "moment":
            names.extend(_string_list(raw_payload.get("related_artists"))[:6])

    if not names:
        names.extend(_string_list(event.get("artist_names")) if isinstance(event, Mapping) else [])
    if not names and candidate.artist_name.strip():
        names.append(candidate.artist_name.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped[:8]


def _resolve_artist(
    client: SpotifyAlbumTracksClient,
    artist_name: str,
    *,
    market: str | None = None,
) -> Mapping[str, Any] | None:
    candidates = client.search_artists(artist_name, limit=5, market=market)
    if not candidates:
        return None

    normalized_target = _normalize_name(artist_name)
    exact_matches = [
        candidate for candidate in candidates
        if _normalize_name(str(candidate.get("name", ""))) == normalized_target
    ]
    ranked = exact_matches or candidates
    return max(ranked, key=lambda item: int(item.get("popularity", 0) or 0))


def _event_raw_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return None
    raw_payload = event.get("raw_payload")
    return raw_payload if isinstance(raw_payload, Mapping) else None


def _mapping_text(mapping: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _iso_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _append_unique(target: list[str], seen: set[str], items: list[str], *, limit: int) -> None:
    for item in items:
        if item in seen:
            continue
        target.append(item)
        seen.add(item)
        if len(target) >= limit:
            return


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            items.append(text)
    return items


def _normalize_name(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def _playlist_track_total(playlist: Mapping[str, Any]) -> int:
    tracks = playlist.get("tracks")
    if isinstance(tracks, Mapping):
        total = tracks.get("total")
        if isinstance(total, int):
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
    total = playlist.get("tracks_total")
    if isinstance(total, int):
        return total
    if isinstance(total, str) and total.isdigit():
        return int(total)
    return 0


def _dry_run_album_track_uris(payload: Mapping[str, Any]) -> list[str]:
    match = payload.get("match")
    album = match.get("spotify_album") if isinstance(match, Mapping) else None
    total_tracks = album.get("total_tracks") if isinstance(album, Mapping) else None
    count = int(total_tracks) if isinstance(total_tracks, int) and total_tracks > 0 else 1
    return [f"spotify:track:dry_run_{index}" for index in range(min(count, 50))]
