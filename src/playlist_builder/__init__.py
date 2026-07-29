"""Spotify SEO playlist builder."""

from playlist_builder.events import EventType, NormalizedEvent
from playlist_builder.festival_research import FestivalResearchResult, load_festival_research
from playlist_builder.tour_research import TourResearchResult, load_tour_research
from playlist_builder.moment_research import MomentResearchResult, load_moment_research
from playlist_builder.research_import import (
    ResearchImportSummary,
    build_research_import_record,
    import_research_files,
    infer_research_kind,
)
from playlist_builder.genius_release_calendar import (
    GeniusReleaseCalendarConfig,
    GeniusReleaseCalendarWatcher,
)
from playlist_builder.spotify_matcher import (
    SpotifyMatchResult,
    SpotifyMatcher,
    SpotifyMatcherConfig,
    SpotifyMatchStatus,
)
from playlist_builder.spotify_prerelease_resolver import (
    ResolvedPrereleaseTrack,
    SpotifyPrereleaseResolution,
    SpotifyPrereleaseResolverConfig,
    SpotifyPrereleaseTrackResolver,
)
from playlist_builder.spotify_prerelease_enrichment import (
    SpotifyPrereleaseEnricher,
    SpotifyPrereleaseEnrichmentConfig,
    SpotifyPrereleaseEnrichmentResult,
)
from playlist_builder.playlist_templates import PlaylistPlan, build_playlist_plan
from playlist_builder.album_pipeline import (
    AlbumValidationOutcome,
    persist_album_validation,
    validate_album_event,
)
from playlist_builder.album_scheduler import AlbumScheduler, AlbumSchedulerResult
from playlist_builder.scoring import (
    ScoreAction,
    ScoreResult,
    ScoringConfig,
    score_opportunity,
)
from playlist_builder.spotify_publisher import PublishResult, SpotifyPublisher
from playlist_builder.persistence import (
    SqliteStore,
    ValidationPersistenceRecord,
    build_validation_record,
    store_from_env,
)
from playlist_builder.spotify_ui_scraper import (
    SpotifyArtistUiScraper,
    SpotifyPrereleaseSnapshot,
    SpotifyPrereleaseTrack,
)
from playlist_builder.watchers import (
    SourceWatcher,
    WatcherContext,
    WatcherFailure,
    WatcherResult,
)

__all__ = [
    "EventType",
    "GeniusReleaseCalendarConfig",
    "GeniusReleaseCalendarWatcher",
    "NormalizedEvent",
    "SourceWatcher",
    "SpotifyMatcher",
    "SpotifyMatcherConfig",
    "SpotifyMatchResult",
    "SpotifyMatchStatus",
    "SpotifyArtistUiScraper",
    "SpotifyPrereleaseSnapshot",
    "SpotifyPrereleaseTrack",
    "ResolvedPrereleaseTrack",
    "SpotifyPrereleaseResolution",
    "SpotifyPrereleaseResolverConfig",
    "SpotifyPrereleaseTrackResolver",
    "SpotifyPrereleaseEnricher",
    "SpotifyPrereleaseEnrichmentConfig",
    "SpotifyPrereleaseEnrichmentResult",
    "AlbumValidationOutcome",
    "validate_album_event",
    "persist_album_validation",
    "AlbumScheduler",
    "AlbumSchedulerResult",
    "FestivalResearchResult",
    "load_festival_research",
    "TourResearchResult",
    "load_tour_research",
    "MomentResearchResult",
    "load_moment_research",
    "ResearchImportSummary",
    "build_research_import_record",
    "import_research_files",
    "infer_research_kind",
    "ScoreAction",
    "ScoreResult",
    "ScoringConfig",
    "score_opportunity",
    "PlaylistPlan",
    "build_playlist_plan",
    "PublishResult",
    "SpotifyPublisher",
    "SqliteStore",
    "store_from_env",
    "ValidationPersistenceRecord",
    "build_validation_record",
    "WatcherContext",
    "WatcherFailure",
    "WatcherResult",
]
