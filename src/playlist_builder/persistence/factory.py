"""Select persistence backend from DATABASE_URL."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from playlist_builder.persistence.env import database_url_from_env
from playlist_builder.persistence.models import CandidateStateRow, PersistenceError
from playlist_builder.persistence.sqlite_store import SqliteStore
from playlist_builder.persistence.validation_record import ValidationPersistenceRecord


class CandidateStore(Protocol):
    def init_schema(self) -> None: ...

    def get_candidate_state(self, dedupe_key: str) -> CandidateStateRow | None: ...

    def list_due_candidates(self, *, as_of): ...  # datetime

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
    ) -> None: ...

    def record_validation(self, record: ValidationPersistenceRecord) -> bool: ...


def store_from_env() -> CandidateStore:
    database_url = database_url_from_env()
    if database_url.startswith("sqlite:///"):
        return SqliteStore.from_url(database_url)
    raise PersistenceError(
        "DATABASE_URL must use sqlite:/// (single-file DB for all pipelines)"
    )
