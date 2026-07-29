"""Scheduled album candidate discovery and due-candidate refresh."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from playlist_builder.album_pipeline import (
    AlbumValidationOutcome,
    event_from_payload,
    persist_album_validation,
    validate_album_event,
)
from playlist_builder.events import NormalizedEvent
from playlist_builder.genius_release_calendar import (
    GeniusReleaseCalendarConfig,
    GeniusReleaseCalendarWatcher,
)
from playlist_builder.persistence.album_tier_cache import (
    AlbumTierCacheGate,
    TierCacheStore,
)
from playlist_builder.spotify_matcher import SpotifyMatcher, SpotifyMatcherConfig
from playlist_builder.spotify_prerelease_enrichment import (
    SpotifyPrereleaseEnricher,
    SpotifyPrereleaseEnrichmentConfig,
)
from playlist_builder.watchers import WatcherContext, WatcherResult


class _TierCacheSkip:
    """Sentinel returned when discovery skips Spotify validation via tier cache."""


_TIER_CACHE_SKIP = _TierCacheSkip()


class SchedulerStore(Protocol):
    def list_due_candidates(self, *, as_of: datetime) -> list[Any]: ...

    def get_latest_payload(self, dedupe_key: str) -> dict[str, Any] | None: ...

    def get_candidate_state(self, dedupe_key: str): ...

    def record_validation(self, record) -> bool: ...


class AlbumSchedulerStore(SchedulerStore, TierCacheStore, Protocol):
    """Persistence access for scheduled album discovery and refresh."""


@dataclass(frozen=True, slots=True)
class AlbumSchedulerSummary:
    discovered_count: int
    refreshed_count: int
    changed_count: int
    publish_pending_count: int
    tier_cache_skipped: int
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "discovered_count": self.discovered_count,
            "refreshed_count": self.refreshed_count,
            "changed_count": self.changed_count,
            "publish_pending_count": self.publish_pending_count,
            "tier_cache_skipped": self.tier_cache_skipped,
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class AlbumSchedulerResult:
    summary: AlbumSchedulerSummary
    discovered: tuple[AlbumValidationOutcome, ...]
    refreshed: tuple[AlbumValidationOutcome, ...]
    genius_failures: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary.to_dict(),
            "discovered": [item.to_dict() for item in self.discovered],
            "refreshed": [item.to_dict() for item in self.refreshed],
            "genius_failures": list(self.genius_failures),
        }


class AlbumScheduler:
    def __init__(
        self,
        *,
        matcher: SpotifyMatcher,
        enricher: SpotifyPrereleaseEnricher | None = None,
        tier_cache_gate: AlbumTierCacheGate | None = None,
    ) -> None:
        self._matcher = matcher
        self._enricher = enricher
        self._tier_cache_gate = tier_cache_gate

    @classmethod
    def from_env(
        cls,
        *,
        market: str | None = None,
        matcher_config: SpotifyMatcherConfig | None = None,
        enricher_config: SpotifyPrereleaseEnrichmentConfig | None = None,
        resolve_prereleases: bool = True,
        headless: bool = True,
        timeout_ms: int = 45_000,
    ) -> "AlbumScheduler":
        config = matcher_config or SpotifyMatcherConfig(market=market)
        if enricher_config is None and config.pre_release_window_days is not None:
            enricher_config = SpotifyPrereleaseEnrichmentConfig(
                pre_release_window_days=config.pre_release_window_days,
            )
        enricher = (
            SpotifyPrereleaseEnricher.from_env(
                market=market,
                headless=headless,
                timeout_ms=timeout_ms,
                config=enricher_config,
            )
            if resolve_prereleases
            else None
        )
        return cls(
            matcher=SpotifyMatcher.from_env(config),
            enricher=enricher,
            tier_cache_gate=AlbumTierCacheGate.from_matcher_config(config),
        )

    def run(
        self,
        store: SchedulerStore,
        *,
        today: date | None = None,
        discover_genius: bool = False,
        genius_year: int | None = None,
        genius_month: int | None = None,
        genius_future_months: int = 0,
        genius_limit: int = 25,
        refresh_due: bool = True,
        due_limit: int = 50,
        persist: bool = True,
    ) -> AlbumSchedulerResult:
        active_today = today or datetime.now(timezone.utc).date()
        checked_at = datetime.now(timezone.utc)
        discovered: list[AlbumValidationOutcome] = []
        refreshed: list[AlbumValidationOutcome] = []
        failures: list[str] = []
        genius_failures: list[dict[str, str]] = []
        tier_cache_skipped = 0
        changed_count = 0
        publish_pending_count = 0

        if discover_genius:
            start_year = genius_year or active_today.year
            start_month = genius_month or active_today.month
            for year, month in _iter_discovery_months(
                start_year,
                start_month,
                genius_future_months,
            ):
                genius_result = self._discover_genius(
                    year=year,
                    month=month,
                    limit=genius_limit,
                )
                genius_failures.extend(
                    {
                        "watcher_name": failure.watcher_name,
                        "message": failure.message,
                        "occurred_at": failure.occurred_at.isoformat(),
                    }
                    for failure in genius_result.failures
                )
                for event in genius_result.events:
                    outcome = self._process_event(
                        event,
                        store=store,
                        today=active_today,
                        checked_at=checked_at,
                        persist=persist,
                        failures=failures,
                        use_tier_cache=True,
                    )
                    if outcome is _TIER_CACHE_SKIP:
                        tier_cache_skipped += 1
                    elif outcome is not None:
                        discovered.append(outcome)

        if refresh_due:
            due_rows = store.list_due_candidates(as_of=checked_at)[:due_limit]
            for row in due_rows:
                outcome = self._refresh_due_row(
                    row,
                    store=store,
                    today=active_today,
                    checked_at=checked_at,
                    persist=persist,
                    failures=failures,
                )
                if outcome is not None:
                    refreshed.append(outcome)

        for outcome in (*discovered, *refreshed):
            if outcome.changed:
                changed_count += 1
            if outcome.publish_pending:
                publish_pending_count += 1

        return AlbumSchedulerResult(
            summary=AlbumSchedulerSummary(
                discovered_count=len(discovered),
                refreshed_count=len(refreshed),
                changed_count=changed_count,
                publish_pending_count=publish_pending_count,
                tier_cache_skipped=tier_cache_skipped,
                failures=tuple(failures),
            ),
            discovered=tuple(discovered),
            refreshed=tuple(refreshed),
            genius_failures=tuple(genius_failures),
        )

    def _discover_genius(
        self,
        *,
        year: int,
        month: int,
        limit: int,
    ) -> WatcherResult:
        return GeniusReleaseCalendarWatcher.from_env(
            GeniusReleaseCalendarConfig(year=year, month=month)
        ).fetch(WatcherContext(limit=limit))

    def _process_event(
        self,
        event: NormalizedEvent,
        *,
        store: SchedulerStore,
        today: date,
        checked_at: datetime,
        persist: bool,
        failures: list[str],
        use_tier_cache: bool = False,
    ) -> AlbumValidationOutcome | None | _TierCacheSkip:
        if (
            use_tier_cache
            and self._tier_cache_gate is not None
            and isinstance(store, TierCacheStore)
        ):
            cached = self._tier_cache_gate.lookup_cached_skip(
                store,
                event,
                as_of=checked_at,
            )
            if cached is not None:
                return _TIER_CACHE_SKIP

        try:
            outcome = validate_album_event(
                event,
                matcher=self._matcher,
                enricher=self._enricher,
                today=today,
                checked_at=checked_at,
            )
            if persist:
                outcome = persist_album_validation(
                    outcome,
                    store,
                    today=today,
                    checked_at=checked_at,
                )
            if (
                persist
                and self._tier_cache_gate is not None
                and isinstance(store, TierCacheStore)
            ):
                self._tier_cache_gate.record_match(
                    store,
                    event,
                    outcome.match,
                    today=today,
                    checked_at=checked_at,
                )
            return outcome
        except Exception as exc:
            failures.append(f"{event.dedupe_key}: {type(exc).__name__}: {exc}")
            return None

    def _refresh_due_row(
        self,
        row,
        *,
        store: SchedulerStore,
        today: date,
        checked_at: datetime,
        persist: bool,
        failures: list[str],
    ) -> AlbumValidationOutcome | None:
        payload = store.get_latest_payload(row.dedupe_key)
        if payload is None:
            failures.append(f"{row.dedupe_key}: missing stored payload")
            return None

        try:
            event = event_from_payload(payload)
        except Exception as exc:
            failures.append(f"{row.dedupe_key}: {type(exc).__name__}: {exc}")
            return None

        return self._process_event(
            event,
            store=store,
            today=today,
            checked_at=checked_at,
            persist=persist,
            failures=failures,
        )


def _iter_discovery_months(
    start_year: int,
    start_month: int,
    future_months: int,
) -> list[tuple[int, int]]:
    if future_months < 0:
        raise ValueError("future_months must not be negative")

    months: list[tuple[int, int]] = []
    year = start_year
    month = start_month
    for _ in range(future_months + 1):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months
