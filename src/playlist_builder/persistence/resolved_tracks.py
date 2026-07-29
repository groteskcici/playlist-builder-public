"""Extract resolved Spotify track URIs from enrichment output."""

from __future__ import annotations

from typing import Any, Mapping

from playlist_builder.spotify_prerelease_enrichment import SpotifyPrereleaseEnrichmentResult


def extract_resolved_track_uris(
    enrichment: SpotifyPrereleaseEnrichmentResult | Mapping[str, Any] | None,
) -> list[str]:
    if enrichment is None:
        return []

    data = enrichment.to_dict() if hasattr(enrichment, "to_dict") else dict(enrichment)
    resolution = data.get("resolution")
    if not isinstance(resolution, Mapping):
        return []

    tracks = resolution.get("tracks")
    if not isinstance(tracks, list):
        return []

    uris: list[str] = []
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        spotify_track = track.get("spotify_track")
        if not isinstance(spotify_track, Mapping):
            continue
        uri = spotify_track.get("uri")
        if isinstance(uri, str) and uri:
            uris.append(uri)
    return sorted(set(uris))
