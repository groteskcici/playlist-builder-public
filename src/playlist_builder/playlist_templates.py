"""Rule-based playlist naming and track selection templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from playlist_builder.persistence.models import CandidateStateRow

if TYPE_CHECKING:
    from playlist_builder.ai.album_planner import AppliedAlbumPlan

_FULL_ALBUM_SUFFIX = " (full album)"


def with_full_album_suffix(title: str, *, max_len: int = 100) -> str:
    """Append `(full album)` once; keep within Spotify playlist name length."""

    text = " ".join(str(title).split()).strip()
    if not text:
        return text
    if text.casefold().endswith("(full album)"):
        return text[:max_len]
    budget = max_len - len(_FULL_ALBUM_SUFFIX)
    if budget < 1:
        return _FULL_ALBUM_SUFFIX.strip()[:max_len]
    base = text[:budget].rstrip()
    return f"{base}{_FULL_ALBUM_SUFFIX}"

@dataclass(frozen=True, slots=True)
class PlaylistPlan:
    template: str
    name: str
    description: str
    track_uris: tuple[str, ...]
    public: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "template": self.template,
            "name": self.name,
            "description": self.description,
            "track_uris": list(self.track_uris),
            "track_count": len(self.track_uris),
            "public": self.public,
        }


def build_playlist_plan(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    *,
    album_track_uris: list[str] | None = None,
    research_track_uris: list[str] | None = None,
    applied_plan: "AppliedAlbumPlan | None" = None,
) -> PlaylistPlan:
    if candidate.effective_status == "pre_release_candidate":
        if applied_plan is not None and applied_plan.track_uris:
            return _applied_album_plan(candidate, applied_plan)
        track_uris = tuple(
            _ordered_prerelease_track_uris(payload) or candidate.resolved_track_uris
        )
        if not track_uris:
            raise ValueError("pre-release candidate has no track URIs to publish")
        return _pre_release_plan(candidate, track_uris)

    if candidate.effective_status == "album_confirmed":
        track_uris = tuple(album_track_uris or [])
        if not track_uris:
            raise ValueError("album-confirmed candidate has no album track URIs to publish")
        return _album_drop_plan(candidate, track_uris)

    if candidate.effective_status == "research_candidate":
        track_uris = tuple(research_track_uris or [])
        if not track_uris:
            raise ValueError("research candidate has no resolved track URIs to publish")
        return _research_plan(candidate, payload, track_uris)

    raise ValueError(
        f"unsupported effective_status for publishing: {candidate.effective_status}"
    )


def _applied_album_plan(
    candidate: CandidateStateRow,
    applied_plan: "AppliedAlbumPlan",
) -> PlaylistPlan:
    template = (
        "pre_release_album"
        if candidate.effective_status == "pre_release_candidate"
        else "album_drop"
    )
    return PlaylistPlan(
        template=template,
        name=with_full_album_suffix(applied_plan.title),
        description=applied_plan.description,
        track_uris=applied_plan.track_uris,
        public=True,
    )


def _pre_release_plan(
    candidate: CandidateStateRow,
    track_uris: tuple[str, ...],
) -> PlaylistPlan:
    artist = candidate.artist_name.strip()
    title = candidate.project_title.strip()
    name = with_full_album_suffix(f"{title} – {artist}")
    description = (
        f"Official singles released so far from {artist}'s upcoming album {title}. "
        "Updated as new tracks drop on Spotify."
    )
    return PlaylistPlan(
        template="pre_release_album",
        name=name,
        description=description,
        track_uris=track_uris,
        public=True,
    )


def _album_drop_plan(
    candidate: CandidateStateRow,
    track_uris: tuple[str, ...],
) -> PlaylistPlan:
    artist = candidate.artist_name.strip()
    title = candidate.project_title.strip()
    name = with_full_album_suffix(f"{title} – {artist}")
    description = (
        f"Full album playlist for {artist} – {title}. "
        "All tracks in album order."
    )
    return PlaylistPlan(
        template="album_drop",
        name=name,
        description=description,
        track_uris=track_uris,
        public=True,
    )


def _research_plan(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
    track_uris: tuple[str, ...],
) -> PlaylistPlan:
    event = payload.get("event")
    raw_payload = event.get("raw_payload") if isinstance(event, Mapping) else None
    title = _raw_text(raw_payload, "suggested_playlist_title") or candidate.project_title.strip()
    description = build_public_research_description(candidate, payload)
    return PlaylistPlan(
        template=str(candidate.event_type),
        name=title,
        description=description,
        track_uris=track_uris,
        public=True,
    )


def build_public_research_description(
    candidate: CandidateStateRow,
    payload: Mapping[str, Any],
) -> str:
    event = payload.get("event")
    raw_payload = event.get("raw_payload") if isinstance(event, Mapping) else None
    raw = raw_payload if isinstance(raw_payload, Mapping) else None

    if candidate.event_type == "festival":
        event_name = _raw_text(raw, "event_name") or candidate.project_title.strip()
        headliners = _string_list_from_mapping(raw, "primary_artists") or [candidate.artist_name.strip()]
        genres = _string_list_from_mapping(raw, "genre_focus")
        description = f"{event_name} lineup playlist with {_join_names(headliners, limit=4)}"
        description += " and more from this year's festival"
        if genres:
            description += f" across {_join_terms(genres, limit=3)}"
        description += "."
        return _clip_description(description)

    if candidate.event_type == "tour":
        headliner = _raw_text(raw, "headliner") or candidate.artist_name.strip()
        tour_name = _raw_text(raw, "tour_name") or candidate.project_title.strip()
        support = _string_list_from_mapping(raw, "supporting_artists")
        description = f"{headliner} tour playlist with essentials and live favorites"
        if support:
            description += f", plus songs from {_join_names(support, limit=2)}"
        description += f" for {_tour_run_phrase(tour_name)}."
        return _clip_description(description)

    if candidate.event_type == "moment":
        event_name = _raw_text(raw, "event_name") or candidate.project_title.strip()
        related = _string_list_from_mapping(raw, "related_artists")
        main_artist = candidate.artist_name.strip()
        supporting = [
            item for item in related
            if not _same_artist_name(item, main_artist)
        ]
        description = f"{main_artist} essentials"
        if supporting:
            description += f" plus key songs from {_join_names(supporting, limit=2)}"
        description += f" around {event_name}."
        return _clip_description(description)

    return _clip_description(f"Music playlist built around {candidate.project_title.strip()}.")


def _ordered_prerelease_track_uris(payload: Mapping[str, Any]) -> list[str]:
    enrichment = payload.get("prerelease_enrichment")
    if not isinstance(enrichment, Mapping):
        return []

    resolution = enrichment.get("resolution")
    if not isinstance(resolution, Mapping):
        return []

    tracks = resolution.get("tracks")
    if not isinstance(tracks, list):
        return []

    uris: list[str] = []
    seen: set[str] = set()
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        spotify_track = track.get("spotify_track")
        if not isinstance(spotify_track, Mapping):
            continue
        uri = spotify_track.get("uri")
        if isinstance(uri, str) and uri and uri not in seen:
            uris.append(uri)
            seen.add(uri)
    return uris


def _string_list_from_mapping(raw_payload: Mapping[str, Any] | None, key: str) -> list[str]:
    if not isinstance(raw_payload, Mapping):
        return []
    value = raw_payload.get(key)
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


def _join_names(items: list[str], *, limit: int) -> str:
    chosen = [item for item in items if item][:limit]
    if not chosen:
        return "featured artists"
    if len(chosen) == 1:
        return chosen[0]
    if len(chosen) == 2:
        return f"{chosen[0]} and {chosen[1]}"
    return f"{', '.join(chosen[:-1])}, and {chosen[-1]}"


def _same_artist_name(left: str, right: str) -> bool:
    left_key = " ".join(left.casefold().split())
    right_key = " ".join(right.casefold().split())
    return left_key == right_key or left_key in right_key or right_key in left_key


def _join_terms(items: list[str], *, limit: int) -> str:
    chosen = [item.strip() for item in items if item][:limit]
    if not chosen:
        return "multiple styles"
    if len(chosen) == 1:
        return chosen[0]
    if len(chosen) == 2:
        return f"{chosen[0]} and {chosen[1]}"
    return f"{', '.join(chosen[:-1])}, and {chosen[-1]}"


def _tour_run_phrase(tour_name: str) -> str:
    """Build 'the {tour} run' without duplicating a leading article."""

    name = " ".join(tour_name.split()).strip()
    if not name:
        return "the tour run"
    lowered = name.casefold()
    if lowered.startswith(("the ", "a ", "an ")):
        return f"{name} run"
    return f"the {name} run"


def _clip_description(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split()).strip()
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    if not clipped:
        return compact[:limit]
    return clipped + "…"


def _raw_text(raw_payload: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(raw_payload, Mapping):
        return None
    value = raw_payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def album_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    match = payload.get("match")
    if not isinstance(match, Mapping):
        return None
    album = match.get("spotify_album")
    if not isinstance(album, Mapping):
        return None
    album_id = album.get("id")
    return str(album_id).strip() if album_id else None
