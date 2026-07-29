"""Source watcher contracts for collecting normalized events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from playlist_builder.events import EventType, NormalizedEvent


@dataclass(frozen=True, slots=True)
class WatcherContext:
    """Runtime inputs shared with source watchers."""

    run_started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    since: datetime | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.run_started_at.tzinfo is None:
            raise ValueError("run_started_at must be timezone-aware")
        if self.since is not None and self.since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class WatcherFailure:
    """Non-fatal watcher failure captured for monitoring."""

    watcher_name: str
    message: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.watcher_name.strip():
            raise ValueError("watcher_name must not be empty")
        if not self.message.strip():
            raise ValueError("message must not be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        object.__setattr__(self, "watcher_name", self.watcher_name.strip())
        object.__setattr__(self, "message", self.message.strip())


@dataclass(frozen=True, slots=True)
class WatcherResult:
    """Result returned by one watcher run."""

    watcher_name: str
    events: tuple[NormalizedEvent, ...] = ()
    failures: tuple[WatcherFailure, ...] = ()

    def __post_init__(self) -> None:
        if not self.watcher_name.strip():
            raise ValueError("watcher_name must not be empty")

        object.__setattr__(self, "watcher_name", self.watcher_name.strip())
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "failures", tuple(self.failures))


class SourceWatcher(Protocol):
    """Interface implemented by each deterministic source watcher."""

    @property
    def name(self) -> str:
        """Stable watcher name for logging and monitoring."""

    @property
    def supported_event_types(self) -> tuple[EventType, ...]:
        """Event categories this watcher can emit."""

    def fetch(self, context: WatcherContext) -> WatcherResult:
        """Fetch raw source data and return normalized events."""
