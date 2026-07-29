"""Persistence layer for candidate state and validation history."""

from playlist_builder.persistence.factory import store_from_env
from playlist_builder.persistence.models import CandidateStateRow, PersistenceError
from playlist_builder.persistence.refresh import compute_next_check_at, track_uri_hash
from playlist_builder.persistence.sqlite_store import SqliteStore
from playlist_builder.persistence.resolved_tracks import extract_resolved_track_uris
from playlist_builder.persistence.validation_record import (
    ValidationPersistenceRecord,
    build_validation_record,
)

__all__ = [
    "CandidateStateRow",
    "PersistenceError",
    "SqliteStore",
    "ValidationPersistenceRecord",
    "build_validation_record",
    "compute_next_check_at",
    "extract_resolved_track_uris",
    "store_from_env",
    "track_uri_hash",
]
