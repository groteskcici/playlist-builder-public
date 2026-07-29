"""Probe Spotify playlist search SERPs for keyword research.

Uses GET /v1/search?type=playlist (same endpoint as competition).
Does not publish anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playlist_builder.spotify_album_watcher import (  # noqa: E402
    SpotifyCredentials,
    SpotifyWebAPIClient,
    load_spotify_env_file,
)
from playlist_builder.spotify_credentials import (  # noqa: E402
    api_credentials_from_env,
    publish_slots_from_env,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_SETLIST_MARKERS = frozenset(
    {"setlist", "set", "tour", "concert", "stadium", "arena", "live"}
)


@dataclass(frozen=True, slots=True)
class ProbeHit:
    playlist_id: str
    name: str
    owner: str
    followers: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.playlist_id,
            "name": self.name,
            "owner": self.owner,
            "followers": self.followers,
        }


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(text)}


def _normalize_title(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.casefold()))


def classify_serp(
    query: str,
    hits: list[ProbeHit],
    *,
    must_keep_tokens: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Heuristic labels for keyword research (not a publish gate)."""

    if not hits:
        return {
            "labels": ["empty"],
            "exact_norm_matches": 0,
            "titles_with_all_must_keep": 0,
            "setlistish_count": 0,
            "spotify_owned_count": 0,
            "notes": ["no playlist hits"],
        }

    query_norm = _normalize_title(query)
    query_tokens = _tokens(query)
    must = {token.casefold() for token in must_keep_tokens if token.strip()}

    exact = 0
    setlistish = 0
    spotify_owned = 0
    with_must = 0
    for hit in hits:
        name_norm = _normalize_title(hit.name)
        name_tokens = _tokens(hit.name)
        if name_norm == query_norm or query_norm in name_norm or name_norm in query_norm:
            exact += 1
        if name_tokens & _SETLIST_MARKERS:
            setlistish += 1
        if hit.owner.casefold() in {"spotify", "spotfiy"} or hit.owner.upper() == "SPOTIFY":
            spotify_owned += 1
        if must and must.issubset(name_tokens):
            with_must += 1

    labels: list[str] = []
    notes: list[str] = []
    if exact >= 2:
        labels.append("exact_phrase_crowded")
        notes.append(f"{exact} near-exact title matches in top results")
    if setlistish >= max(3, len(hits) // 3):
        labels.append("setlist_tour_swamp")
        notes.append(f"{setlistish}/{len(hits)} titles look setlist/tour/live")
    if spotify_owned >= 2:
        labels.append("official_spotify_heavy")
        notes.append(f"{spotify_owned} Spotify-owned playlists in top results")
    if must and with_must == 0:
        labels.append("qualifier_ignored")
        notes.append(
            "none of the top titles contain all must-keep tokens: "
            + ", ".join(sorted(must))
        )
    # Song-token gravity: query has a rare qualifier but results share only the big tokens
    if must and with_must == 0 and query_tokens - must:
        labels.append("big_token_eats_query")
        notes.append("Spotify likely ranking on the non-qualifier tokens only")
    if not labels:
        labels.append("mixed_or_unclear")
        notes.append("no strong heuristic fired; inspect titles manually")

    return {
        "labels": labels,
        "exact_norm_matches": exact,
        "titles_with_all_must_keep": with_must,
        "setlistish_count": setlistish,
        "spotify_owned_count": spotify_owned,
        "top_owner_counts": dict(Counter(hit.owner for hit in hits).most_common(5)),
        "notes": notes,
    }


def probe_query(
    client: SpotifyWebAPIClient,
    query: str,
    *,
    limit: int = 15,
    market: str | None = None,
    fetch_followers: bool = True,
    must_keep_tokens: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw = client.search_playlists(query, limit=limit, market=market)
    hits: list[ProbeHit] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        playlist_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not playlist_id or not name:
            continue
        owner_payload = item.get("owner")
        owner = ""
        if isinstance(owner_payload, Mapping):
            owner = str(
                owner_payload.get("display_name") or owner_payload.get("id") or ""
            ).strip()
        followers: int | None = None
        if fetch_followers:
            details = client.get_playlist(playlist_id)
            if isinstance(details, Mapping):
                followers_payload = details.get("followers")
                if isinstance(followers_payload, Mapping):
                    total = followers_payload.get("total")
                    if isinstance(total, int):
                        followers = total
                if not owner:
                    owner_details = details.get("owner")
                    if isinstance(owner_details, Mapping):
                        owner = str(
                            owner_details.get("display_name")
                            or owner_details.get("id")
                            or ""
                        ).strip()
        hits.append(
            ProbeHit(
                playlist_id=playlist_id,
                name=name,
                owner=owner or "unknown",
                followers=followers,
            )
        )

    return {
        "query": query,
        "market": market,
        "hit_count": len(hits),
        "classification": classify_serp(query, hits, must_keep_tokens=must_keep_tokens),
        "hits": [hit.to_dict() for hit in hits],
    }


def _client_from_env() -> SpotifyWebAPIClient:
    from playlist_builder.spotify_album_watcher import SpotifyAPIError

    load_spotify_env_file(Path(__file__).resolve().parents[1] / ".env")
    candidates: list[SpotifyCredentials] = []

    # Prefer publish slots first for local probes — API refresh is often stale on
    # the Windows checkout while publish tokens were recently re-authed on VPS.
    for slot in publish_slots_from_env():
        candidates.append(
            SpotifyCredentials(
                client_id=slot.client_id,
                client_secret=slot.client_secret,
                refresh_token=slot.refresh_token,
            )
        )
    try:
        api = api_credentials_from_env()
        if api.refresh_token or api.access_token:
            candidates.append(api)
    except Exception:
        pass

    if not candidates:
        raise RuntimeError(
            "No Spotify credentials: set SPOTIFY_PUBLISH_1_* or SPOTIFY_API_* "
            "(client id/secret + refresh token)"
        )

    errors: list[str] = []
    for credentials in candidates:
        client = SpotifyWebAPIClient(credentials)
        try:
            client.search_playlists("test", limit=1)
            return client
        except SpotifyAPIError as exc:
            errors.append(str(exc))
            continue
    joined = "; ".join(errors) if errors else "unknown auth failure"
    raise RuntimeError(f"All Spotify credentials failed for playlist search: {joined}")


# Default batch = curated playlist-search review queries.
DEFAULT_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ccma awards 2026", ("ccma",)),
    ("gorillaz at electric picnic 2026", ("picnic",)),
    ("BTS at iHeartRadio Music Festival 2026", ("iheartradio", "festival")),
    ("hermoso - benny blanco", ("hermoso",)),
    ("hermoso – benny blanco", ("hermoso",)),
    ("camp rock 3 soundtrack", ("soundtrack",)),
    ("AC/DC 2026 Tour", ("tour",)),
    ("the black parade 20th anniversary", ("anniversary", "20th")),
    ("dont look back in anger documentary", ("documentary",)),
)


def _payload_title(payload: Mapping[str, Any] | None, fallback: str) -> str:
    if not isinstance(payload, Mapping):
        return fallback
    comp = payload.get("competition_evaluation")
    if isinstance(comp, Mapping):
        for key in ("revised_title", "title"):
            value = str(comp.get(key) or "").strip()
            if value:
                return value
    event = payload.get("event")
    if isinstance(event, Mapping):
        raw = event.get("raw_payload")
        if isinstance(raw, Mapping):
            value = str(raw.get("suggested_playlist_title") or "").strip()
            if value:
                return value
        value = str(event.get("title") or "").strip()
        if value:
            return value
    return fallback


def load_published_from_db(database_path: Path) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(str(database_path))
    try:
        rows = conn.execute(
            """
            SELECT
                dedupe_key, source, event_type, artist_name, project_title,
                spotify_playlist_id, last_published_at, latest_payload,
                spotify_publish_slot
            FROM candidate_state
            WHERE spotify_playlist_id IS NOT NULL
              AND TRIM(spotify_playlist_id) != ''
            ORDER BY last_published_at ASC
            """
        ).fetchall()
    finally:
        conn.close()

    published: list[dict[str, Any]] = []
    for row in rows:
        (
            dedupe_key,
            source,
            event_type,
            artist_name,
            project_title,
            playlist_id,
            published_at,
            payload_raw,
            publish_slot,
        ) = row
        payload: Mapping[str, Any] | None = None
        if payload_raw:
            try:
                parsed = json.loads(payload_raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = None
        stored_title = _payload_title(payload, str(project_title or "").strip())
        published.append(
            {
                "dedupe_key": dedupe_key,
                "source": source,
                "event_type": event_type,
                "artist_name": artist_name,
                "project_title": project_title,
                "stored_title": stored_title,
                "spotify_playlist_id": playlist_id,
                "last_published_at": published_at,
                "spotify_publish_slot": publish_slot,
            }
        )
    return published


def _our_rank(hits: list[dict[str, Any]], playlist_id: str) -> int | None:
    target = playlist_id.strip()
    for index, hit in enumerate(hits, start=1):
        if str(hit.get("id") or "").strip() == target:
            return index
    return None


def probe_published_batch(
    client: SpotifyWebAPIClient,
    published: list[dict[str, Any]],
    *,
    limit: int,
    market: str | None,
    fetch_followers: bool,
    use_live_title: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, row in enumerate(published, start=1):
        playlist_id = str(row["spotify_playlist_id"])
        query = str(row["stored_title"] or "").strip()
        live_title = None
        if use_live_title:
            try:
                details = client.get_playlist(playlist_id)
                if isinstance(details, Mapping):
                    live_title = str(details.get("name") or "").strip() or None
            except Exception as exc:  # noqa: BLE001 — keep batch going
                live_title = None
                row = {**row, "live_title_error": str(exc)}
            if live_title:
                query = live_title
        if not query:
            results.append(
                {
                    **row,
                    "query": "",
                    "live_title": live_title,
                    "error": "no title available",
                }
            )
            continue

        print(
            f"[{index}/{len(published)}] probing: {query}",
            file=sys.stderr,
            flush=True,
        )
        probed = probe_query(
            client,
            query,
            limit=limit,
            market=market,
            fetch_followers=fetch_followers,
        )
        hits = probed.get("hits") or []
        rank = _our_rank(hits, playlist_id)
        results.append(
            {
                **row,
                "query": query,
                "live_title": live_title,
                "our_rank": rank,
                "we_visible": rank is not None,
                "classification": probed.get("classification"),
                "hit_count": probed.get("hit_count"),
                "hits": hits,
            }
        )
    return results


def _summarize_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for row in results:
        hits = row.get("hits") or []
        classification = row.get("classification") or {}
        item: dict[str, Any] = {
            "query": row.get("query"),
            "labels": classification.get("labels"),
            "notes": classification.get("notes"),
            "top_titles": [hit.get("name") for hit in hits[:5]],
        }
        if "we_visible" in row:
            item["we_visible"] = row.get("we_visible")
            item["our_rank"] = row.get("our_rank")
            item["spotify_playlist_id"] = row.get("spotify_playlist_id")
            item["source"] = row.get("source")
            item["event_type"] = row.get("event_type")
            item["live_title"] = row.get("live_title")
            item["stored_title"] = row.get("stored_title")
        summary.append(item)
    return summary


def _rollup(results: list[dict[str, Any]]) -> dict[str, Any]:
    labels: Counter[str] = Counter()
    visible = 0
    missing = 0
    by_source: Counter[str] = Counter()
    missing_by_source: Counter[str] = Counter()
    for row in results:
        classification = row.get("classification") or {}
        for label in classification.get("labels") or []:
            labels[str(label)] += 1
        source = str(row.get("source") or "unknown")
        by_source[source] += 1
        if row.get("we_visible"):
            visible += 1
        else:
            missing += 1
            missing_by_source[source] += 1
    return {
        "total": len(results),
        "we_visible": visible,
        "we_missing": missing,
        "label_counts": dict(labels.most_common()),
        "by_source": dict(by_source.most_common()),
        "missing_by_source": dict(missing_by_source.most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research Spotify playlist SERPs for keyword learning (read-only)."
    )
    parser.add_argument(
        "queries",
        nargs="*",
        help="Queries to probe. If omitted, runs the default SERP-review batch.",
    )
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--market", default=os.environ.get("PLAYLIST_MARKET", "DE"))
    parser.add_argument(
        "--no-followers",
        action="store_true",
        help="Skip per-playlist follower lookups (faster, less detail).",
    )
    parser.add_argument(
        "--must-keep",
        action="append",
        default=[],
        help="Token that should appear in result titles (repeatable). "
        "Only applied when passing explicit queries.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print classification + top 5 titles only.",
    )
    parser.add_argument(
        "--from-db",
        nargs="?",
        const="data/playlist_builder.db",
        default=None,
        help="Probe all published playlists from SQLite "
        "(default path: data/playlist_builder.db).",
    )
    parser.add_argument(
        "--stored-title",
        action="store_true",
        help="With --from-db, search the stored/competition title instead of "
        "fetching the live Spotify playlist name.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write full JSON results to this path (in addition to stdout).",
    )
    args = parser.parse_args()

    client = _client_from_env()
    results: list[dict[str, Any]] = []
    rollup: dict[str, Any] | None = None

    if args.from_db:
        db_path = Path(args.from_db)
        if not db_path.is_file():
            raise SystemExit(f"database not found: {db_path}")
        published = load_published_from_db(db_path)
        print(
            f"Loaded {len(published)} published playlists from {db_path}",
            file=sys.stderr,
            flush=True,
        )
        results = probe_published_batch(
            client,
            published,
            limit=args.limit,
            market=args.market or None,
            fetch_followers=not args.no_followers,
            use_live_title=not args.stored_title,
        )
        rollup = _rollup(results)
    elif args.queries:
        must = tuple(args.must_keep)
        for query in args.queries:
            results.append(
                probe_query(
                    client,
                    query,
                    limit=args.limit,
                    market=args.market or None,
                    fetch_followers=not args.no_followers,
                    must_keep_tokens=must,
                )
            )
    else:
        for query, must in DEFAULT_PROBES:
            results.append(
                probe_query(
                    client,
                    query,
                    limit=args.limit,
                    market=args.market or None,
                    fetch_followers=not args.no_followers,
                    must_keep_tokens=must,
                )
            )

    if args.summary_only:
        payload: Any = _summarize_rows(results)
        if rollup is not None:
            payload = {"rollup": rollup, "playlists": payload}
    else:
        payload = results if rollup is None else {"rollup": rollup, "playlists": results}

    text = json.dumps(payload, indent=2, ensure_ascii=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
