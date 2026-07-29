"""High-level setlist.fm lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Mapping

from playlist_builder.setlist_fm.client import (
    DEFAULT_SEARCH_MAX_PAGES,
    SetlistFmClient,
    SetlistFmError,
)
from playlist_builder.setlist_fm.models import ParsedSetlist
from playlist_builder.setlist_fm.parse import format_setlist_date, parse_setlist


@dataclass(frozen=True, slots=True)
class SetlistArtistMatch:
    mbid: str
    name: str
    sort_name: str | None
    disambiguation: str | None
    url: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "mbid": self.mbid,
            "name": self.name,
            "sort_name": self.sort_name,
            "disambiguation": self.disambiguation,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class SetlistLookupResult:
    artist: SetlistArtistMatch
    setlist: ParsedSetlist
    match_score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "artist": self.artist.to_dict(),
            "match_score": round(self.match_score, 4),
            "setlist": self.setlist.to_dict(),
        }


class SetlistLookup:
    def __init__(self, client: SetlistFmClient) -> None:
        self._client = client

    def find_artist(self, artist_name: str) -> SetlistArtistMatch | None:
        candidates: list[Mapping[str, Any]] = []
        best: tuple[Mapping[str, Any], float] | None = None

        for query_name in _artist_query_variants(artist_name):
            for page in range(1, 4):
                try:
                    page_results = self._client.search_artists(
                        artist_name=query_name,
                        page=page,
                    )
                except SetlistFmError as exc:
                    if "page does not exist" in str(exc) or "HTTP 404" in str(exc):
                        break
                    raise
                if not page_results:
                    break

                for candidate in page_results:
                    candidates.append(candidate)
                    score = _score_artist_candidate(artist_name, candidate)
                    if best is None or score > best[1]:
                        best = (candidate, score)

                if best is not None and best[1] >= 0.99:
                    break
                if len(page_results) < 30:
                    break

        if best is None:
            return None

        best_candidate, score = best
        mbid = best_candidate.get("mbid")
        name = best_candidate.get("name")
        if not isinstance(mbid, str) or not mbid or not isinstance(name, str) or not name:
            return None
        if score < 0.75:
            return None

        return SetlistArtistMatch(
            mbid=mbid,
            name=name,
            sort_name=_optional_str(best_candidate.get("sortName")),
            disambiguation=_optional_str(best_candidate.get("disambiguation")),
            url=_optional_str(best_candidate.get("url")),
        )

    def find_setlist_for_show(
        self,
        *,
        artist_name: str,
        event_date: date | None = None,
        city_name: str | None = None,
        venue_name: str | None = None,
        country_code: str | None = None,
        year: int | None = None,
    ) -> SetlistLookupResult | None:
        artist = self.find_artist(artist_name)
        if artist is None:
            return None

        search_year = year or (event_date.year if event_date else None)
        date_param = format_setlist_date(event_date) if event_date else None

        setlists, _total = self._client.search_setlists(
            artist_mbid=artist.mbid,
            city_name=city_name,
            venue_name=venue_name,
            country_code=country_code,
            date=date_param,
            year=search_year,
        )

        if not setlists and search_year is not None:
            setlists, _total = self._client.search_setlists(
                artist_mbid=artist.mbid,
                city_name=city_name,
                venue_name=venue_name,
                country_code=country_code,
                year=search_year,
            )

        if not setlists:
            setlists, _total = self._client.get_artist_setlists(artist.mbid)

        if not setlists:
            return None

        best_raw, score = _best_setlist_match(
            setlists,
            event_date=event_date,
            city_name=city_name,
            venue_name=venue_name,
        )
        parsed = _hydrate_setlist(self._client, best_raw)
        return SetlistLookupResult(artist=artist, setlist=parsed, match_score=score)

    def list_recent_setlists(
        self,
        artist_name: str,
        *,
        year: int | None = None,
        limit: int = 10,
    ) -> list[ParsedSetlist]:
        artist = self.find_artist(artist_name)
        if artist is None:
            return []

        if year is not None:
            raw_setlists = list(
                self._client.iter_search_setlists(
                    artist_mbid=artist.mbid,
                    year=year,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )
        else:
            raw_setlists = list(
                self._client.iter_artist_setlists(
                    artist.mbid,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )

        parsed = [parse_setlist(raw) for raw in raw_setlists[:limit]]
        parsed.sort(key=lambda item: item.event_date, reverse=True)
        return parsed

    def find_canonical_tour_setlist(
        self,
        *,
        artist_name: str,
        tour_name: str,
        tour_year: int,
        artist_mbid: str | None = None,
        event_date: date | None = None,
    ) -> SetlistLookupResult | None:
        artist = (
            SetlistArtistMatch(
                mbid=artist_mbid,
                name=artist_name,
                sort_name=None,
                disambiguation=None,
                url=None,
            )
            if artist_mbid
            else self.find_artist(artist_name)
        )
        if artist is None:
            fallback_mbid = artist_mbid or "artist-name-fallback"
            artist = SetlistArtistMatch(
                mbid=fallback_mbid,
                name=artist_name,
                sort_name=None,
                disambiguation=None,
                url=None,
            )

        raw_setlists: list[Mapping[str, Any]] = []
        for variant in _tour_name_variants(tour_name):
            raw_setlists = list(
                self._iter_search_setlists_safe(
                    artist_name=artist_name,
                    tour_name=variant,
                    year=tour_year,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )
            if raw_setlists:
                break

        if not raw_setlists:
            raw_setlists = list(
                self._iter_search_setlists_safe(
                    artist_name=artist_name,
                    year=tour_year,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )

        if not raw_setlists and artist_mbid:
            raw_setlists = list(
                self._iter_search_setlists_safe(
                    artist_mbid=artist_mbid,
                    year=tour_year,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )

        if not raw_setlists and artist.mbid and artist.mbid != "artist-name-fallback":
            raw_setlists = list(
                self._iter_artist_setlists_safe(
                    artist.mbid,
                    max_pages=DEFAULT_SEARCH_MAX_PAGES,
                )
            )

        candidates = _select_tour_candidates(
            raw_setlists,
            artist_name=artist_name,
            tour_name=tour_name,
            tour_year=tour_year,
            event_date=event_date,
        )
        if not candidates:
            return None

        for summary, score in candidates:
            hydrated = _hydrate_setlist(self._client, {"id": summary.setlist_id})
            if hydrated.live_song_count <= 0:
                continue
            return SetlistLookupResult(artist=artist, setlist=hydrated, match_score=score)

        return None

    def _iter_search_setlists_safe(self, **kwargs: Any):
        try:
            yield from self._client.iter_search_setlists(**kwargs)
        except SetlistFmError as exc:
            if "not found" in str(exc).lower() or "http 404" in str(exc).lower():
                return
            raise

    def _iter_artist_setlists_safe(self, artist_mbid: str, *, max_pages: int):
        try:
            yield from self._client.iter_artist_setlists(artist_mbid, max_pages=max_pages)
        except SetlistFmError as exc:
            if "not found" in str(exc).lower() or "http 404" in str(exc).lower():
                return
            raise


def _score_artist_candidate(
    query_name: str,
    candidate: Mapping[str, Any],
) -> float:
    candidate_name = str(candidate.get("name", ""))
    candidate_sort_name = str(candidate.get("sortName") or "")
    disambiguation = str(candidate.get("disambiguation") or "")
    score = max(
        _similarity(query_name, candidate_name),
        _similarity(query_name, _canonical_sort_name(candidate_sort_name)),
    )

    if _normalize(query_name) in {
        _normalize(candidate_name),
        _normalize(_canonical_sort_name(candidate_sort_name)),
    }:
        score = max(score, 1.0)

    query_lower = query_name.lower()
    candidate_lower = candidate_name.lower()
    disambiguation_lower = disambiguation.lower()

    if " feat" not in query_lower and " & " not in query_lower:
        if " feat" in candidate_lower or " and " in candidate_lower or " & " in candidate_lower:
            score -= 0.35

    for keyword in ("tribute", "parody", "cover", "karaoke"):
        if keyword in disambiguation_lower or keyword in candidate_lower:
            score -= 0.45

    return score


def _best_setlist_match(
    setlists: list[Mapping[str, Any]],
    *,
    event_date: date | None,
    city_name: str | None,
    venue_name: str | None,
) -> tuple[Mapping[str, Any], float]:
    scored: list[tuple[Mapping[str, Any], float]] = []
    for raw in setlists:
        score = 0.0
        parsed = parse_setlist(raw)
        if event_date and parsed.event_date == event_date:
            score += 1.0
        if city_name and parsed.city_name:
            score += 0.5 * _similarity(city_name, parsed.city_name)
        if venue_name and parsed.venue_name:
            score += 0.5 * _similarity(venue_name, parsed.venue_name)
        scored.append((raw, score))

    if all(score == 0.0 for _raw, score in scored):
        return setlists[0], 0.0
    return max(scored, key=lambda item: item[1])


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in value).split()
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tour_name_matches(expected_tour_name: str, raw: Mapping[str, Any]) -> bool:
    tour = raw.get("tour")
    if not isinstance(tour, Mapping):
        return False
    actual = tour.get("name")
    if not isinstance(actual, str) or not actual.strip():
        return False
    return _tour_similarity(expected_tour_name, actual) >= 0.82


def _artist_query_variants(artist_name: str) -> list[str]:
    variants = [artist_name.strip()]
    lowered = artist_name.strip().lower()
    if lowered.startswith("the "):
        variants.append(artist_name.strip()[4:])
    return [variant for variant in variants if variant]


def _canonical_sort_name(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if ", the" in value.lower():
        left, _, right = value.rpartition(",")
        if right.strip().lower() == "the":
            return f"The {left.strip()}"
    return value


def _tour_name_variants(tour_name: str) -> list[str]:
    cleaned = tour_name.strip()
    variants = [cleaned]
    normalized = _strip_tour_suffixes(cleaned)
    if normalized and normalized not in variants:
        variants.append(normalized)
    compact = " ".join(word for word in normalized.split() if not word.isdigit())
    if compact and compact not in variants:
        variants.append(compact)
    return variants


def _strip_tour_suffixes(value: str) -> str:
    text = " ".join(value.split())
    suffixes = [" world tour", " tour", " residency", " live 2026", " world tour 2026"]
    lowered = text.lower()
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if lowered.endswith(suffix):
                text = text[: -len(suffix)].strip(" -")
                lowered = text.lower()
                changed = True
    return text


def _tour_similarity(left: str, right: str) -> float:
    variants_left = _tour_name_variants(left)
    variants_right = _tour_name_variants(right)
    return max(_similarity(a, b) for a in variants_left for b in variants_right)


def _select_tour_candidates(
    raw_setlists: list[Mapping[str, Any]],
    *,
    artist_name: str,
    tour_name: str,
    tour_year: int,
    event_date: date | None,
) -> list[tuple[ParsedSetlist, float]]:
    scored: list[tuple[ParsedSetlist, float]] = []
    for raw in raw_setlists:
        summary = parse_setlist(raw)
        if summary.event_date.year != tour_year:
            continue
        score = 0.0
        if _normalize(summary.artist_name) == _normalize(artist_name):
            score += 1.0
        tour = raw.get("tour") if isinstance(raw.get("tour"), Mapping) else None
        actual_tour = str(tour.get("name")) if isinstance(tour, Mapping) else ""
        if actual_tour.strip():
            score += 2.0 * _tour_similarity(tour_name, actual_tour)
        if event_date is not None:
            score += _date_proximity_score(summary.event_date, event_date)
            if summary.event_date <= event_date:
                score += 0.75
        scored.append((summary, score))

    scored.sort(
        key=lambda item: (
            item[1],
            1 if event_date is not None and item[0].event_date <= event_date else 0,
            item[0].event_date,
        ),
        reverse=True,
    )
    strong = [item for item in scored if item[1] >= 1.5]
    return strong or scored


def _date_proximity_score(candidate_date: date, target_date: date) -> float:
    if candidate_date <= target_date:
        delta_days = (target_date - candidate_date).days
        return max(0.0, 1.25 - min(delta_days, 120) / 120)
    delta_days = (candidate_date - target_date).days
    return max(0.0, 0.45 - min(delta_days, 120) / 240)


def _hydrate_setlist(client: SetlistFmClient, raw: Mapping[str, Any]) -> ParsedSetlist:
    setlist_id = raw.get("id")
    if isinstance(setlist_id, str) and setlist_id.strip():
        return parse_setlist(client.get_setlist(setlist_id.strip()))
    return parse_setlist(raw)
