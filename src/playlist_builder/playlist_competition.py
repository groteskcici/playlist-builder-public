"""Spotify playlist competition checks for album playlist opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from playlist_builder.spotify_matcher import _normalize, _similarity


class PlaylistCompetitionClient(Protocol):
    def search_playlists(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def get_playlist(self, playlist_id: str) -> Mapping[str, Any]: ...


class PlaylistCompetitionSkipError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlaylistCompetitionConfig:
    max_queries: int = 3
    query_limit: int = 10
    min_title_similarity: float = 0.72
    exact_title_similarity: float = 0.88
    skip_relevant_count: int = 8


@dataclass(frozen=True, slots=True)
class PlaylistCompetitionMatch:
    playlist_id: str
    name: str
    owner_name: str | None
    followers: int
    query: str
    title_similarity: float
    exact_title_match: bool
    has_target_year: bool
    playlist_url: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "owner_name": self.owner_name,
            "followers": self.followers,
            "query": self.query,
            "title_similarity": round(self.title_similarity, 4),
            "exact_title_match": self.exact_title_match,
            "has_target_year": self.has_target_year,
            "playlist_url": self.playlist_url,
        }


@dataclass(frozen=True, slots=True)
class PlaylistCompetitionResult:
    should_skip: bool
    reason: str
    queries: tuple[str, ...]
    relevant_count: int
    competitors: tuple[PlaylistCompetitionMatch, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "should_skip": self.should_skip,
            "reason": self.reason,
            "queries": list(self.queries),
            "relevant_count": self.relevant_count,
            "competitors": [match.to_dict() for match in self.competitors],
        }


class PlaylistCompetitionChecker:
    def __init__(
        self,
        client: PlaylistCompetitionClient,
        *,
        config: PlaylistCompetitionConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or PlaylistCompetitionConfig()

    def evaluate(
        self,
        *,
        event_name: str,
        event_date: str | None,
        market: str | None = None,
        exclude_playlist_ids: set[str] | None = None,
        queries: tuple[str, ...] | None = None,
        compare_names: tuple[str, ...] | None = None,
    ) -> PlaylistCompetitionResult:
        target_year = event_date[:4] if event_date and len(event_date) >= 4 else None
        queries = queries or _dedupe_queries([event_name, f"{event_name} playlist"], max_queries=self._config.max_queries)
        query_bases = compare_names or (event_name,)
        excluded = {item for item in (exclude_playlist_ids or set()) if item}

        matches_by_id: dict[str, PlaylistCompetitionMatch] = {}
        for query in queries:
            for candidate in self._client.search_playlists(
                query,
                limit=self._config.query_limit,
                market=market,
            ):
                if not isinstance(candidate, Mapping):
                    continue
                playlist_id = str(candidate.get("id") or "").strip()
                if not playlist_id or playlist_id in excluded:
                    continue
                match = self._score_candidate(
                    candidate,
                    query=query,
                    query_bases=query_bases,
                    target_year=target_year,
                )
                if match is None:
                    continue
                existing = matches_by_id.get(playlist_id)
                if existing is None or (match.followers, match.title_similarity) > (
                    existing.followers,
                    existing.title_similarity,
                ):
                    matches_by_id[playlist_id] = match

        competitors = tuple(
            sorted(
                matches_by_id.values(),
                key=lambda item: (
                    item.exact_title_match,
                    item.title_similarity,
                    item.has_target_year,
                    item.followers,
                ),
                reverse=True,
            )
        )

        relevant_count = len(competitors)
        should_skip = relevant_count >= self._config.skip_relevant_count
        reason = "crowded_keyword" if should_skip else "low_competition"

        return PlaylistCompetitionResult(
            should_skip=should_skip,
            reason=reason,
            queries=queries,
            relevant_count=relevant_count,
            competitors=competitors,
        )

    def _score_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        query: str,
        query_bases: tuple[str, ...],
        target_year: str | None,
    ) -> PlaylistCompetitionMatch | None:
        name = str(candidate.get("name") or "").strip()
        if not name:
            return None
        if target_year and _contains_conflicting_year(name, target_year):
            return None

        title_similarity = max(_similarity(base, name) for base in query_bases)
        normalized_name = _normalize(name)
        exact_title_match = (
            title_similarity >= self._config.exact_title_similarity
            or any(_normalize(base) in normalized_name for base in query_bases)
        )
        if title_similarity < self._config.min_title_similarity and not exact_title_match:
            return None

        playlist_id = str(candidate.get("id") or "").strip()
        details = self._client.get_playlist(playlist_id)
        if not isinstance(details, Mapping):
            details = {}
        followers_payload = details.get("followers")
        followers = 0
        if isinstance(followers_payload, Mapping):
            raw_total = followers_payload.get("total")
            if isinstance(raw_total, int):
                followers = raw_total

        owner_payload = details.get("owner")
        owner_name = None
        if isinstance(owner_payload, Mapping):
            raw_owner = owner_payload.get("display_name") or owner_payload.get("id")
            if isinstance(raw_owner, str) and raw_owner.strip():
                owner_name = raw_owner.strip()

        external_urls = details.get("external_urls")
        playlist_url = None
        if isinstance(external_urls, Mapping):
            raw_url = external_urls.get("spotify")
            if isinstance(raw_url, str) and raw_url.strip():
                playlist_url = raw_url.strip()

        return PlaylistCompetitionMatch(
            playlist_id=playlist_id,
            name=name,
            owner_name=owner_name,
            followers=followers,
            query=query,
            title_similarity=title_similarity,
            exact_title_match=exact_title_match,
            has_target_year=bool(target_year and target_year in name),
            playlist_url=playlist_url,
        )


def build_album_playlist_queries(
    artist_name: str,
    project_title: str,
    event_date: str | None,
    *,
    max_queries: int = 3,
) -> tuple[str, ...]:
    year = event_date[:4] if event_date and len(event_date) >= 4 else ""
    base = f"{artist_name.strip()} {project_title.strip()}".strip()
    variants = [
        base,
        f"{project_title.strip()} {artist_name.strip()}".strip(),
        f"{base} {year}".strip() if year and year not in base else f"{base} playlist".strip(),
        f"{base} playlist".strip(),
    ]
    return _dedupe_queries(variants, max_queries=max_queries)


def _dedupe_queries(variants: list[str], *, max_queries: int) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = _normalize(variant)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(variant.strip())
        if len(deduped) >= max_queries:
            break
    return tuple(deduped)


def _contains_conflicting_year(value: str, target_year: str) -> bool:
    years = {
        token
        for token in value.split()
        if len(token) == 4 and token.isdigit() and token.startswith("20")
    }
    return bool(years and target_year not in years)
