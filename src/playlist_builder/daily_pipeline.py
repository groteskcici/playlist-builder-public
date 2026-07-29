"""Unified daily playlist pipeline: discover → compete → build → publish."""

from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from playlist_builder.ai.album_competition import generate_album_competition_with_retitle
from playlist_builder.ai.codex_cli import CodexCliConfig, CodexCliError, CodexCliRunner
from playlist_builder.ai.queue import AiQueue
from playlist_builder.ai.research_competition import (
    MissingResearchCompetitionQueriesError,
    generate_research_competition_with_retitle,
)
from playlist_builder.ai.research_generation import berlin_run_date, generate_research_json
from playlist_builder.research_dedupe import (
    extend_research_index_from_payload,
    load_existing_research_index,
)
from playlist_builder.album_scheduler import AlbumScheduler
from playlist_builder.daily_publish_state import (
    DEFAULT_DAILY_CAP,
    DEFAULT_DAILY_FLOOR,
    load_daily_publish_state,
    record_daily_publish,
    remaining_daily_capacity,
)
from playlist_builder.persistence.env import project_root
from playlist_builder.pipeline_alerts import send_pipeline_alert
from playlist_builder.playlist_competition import PlaylistCompetitionSkipError
from playlist_builder.research_import import import_research_files
from playlist_builder.scoring import ScoreAction
from playlist_builder.spotify_matcher import SpotifyMatcherConfig
from playlist_builder.spotify_prerelease_enrichment import SpotifyPrereleaseEnrichmentConfig
from playlist_builder.spotify_publisher import PlaylistPublishSkipError, SpotifyPublisher


ALBUM_STATUSES = frozenset({"pre_release_candidate", "album_confirmed"})


@dataclass
class DailyPipelineConfig:
    market: str = "DE"
    daily_cap: int = DEFAULT_DAILY_CAP
    daily_floor: int = DEFAULT_DAILY_FLOOR
    stretch_seconds: float = 2.0
    genius_future_months: int = 5
    genius_limit: int = 25
    dry_run: bool = False
    skip_discover: bool = False
    skip_research_gen: bool = False
    skip_publish: bool = False


@dataclass
class DailyPipelineResult:
    album_discovery_ok: bool = True
    album_discovery_error: str | None = None
    research_generation: list[dict[str, Any]] = field(default_factory=list)
    research_generation_errors: list[dict[str, Any]] = field(default_factory=list)
    research_import: dict[str, Any] | None = None
    processed: list[dict[str, Any]] = field(default_factory=list)
    published_count: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    fatal_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "album_discovery_ok": self.album_discovery_ok,
            "album_discovery_error": self.album_discovery_error,
            "research_generation": self.research_generation,
            "research_generation_errors": self.research_generation_errors,
            "research_import": self.research_import,
            "processed": self.processed,
            "published_count": self.published_count,
            "skipped": self.skipped,
            "alerts": self.alerts,
            "fatal_error": self.fatal_error,
        }


def run_daily_pipeline(
    store: Any,
    *,
    config: DailyPipelineConfig | None = None,
    scheduler: AlbumScheduler | None = None,
    competition_client: Any | None = None,
    catalog_client: Any | None = None,
    publisher: SpotifyPublisher | None = None,
    runner: CodexCliRunner | None = None,
    queue: AiQueue | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    alert_fn: Callable[..., dict[str, Any]] | None = None,
    today: date | None = None,
) -> DailyPipelineResult:
    cfg = config or DailyPipelineConfig()
    sleeper = sleep_fn or time.sleep
    alerter = alert_fn or send_pipeline_alert
    day = today or date.today()
    result = DailyPipelineResult()

    try:
        _run_discovery_phase(
            store,
            cfg=cfg,
            scheduler=scheduler,
            result=result,
            alerter=alerter,
            sleeper=sleeper,
            day=day,
        )
        _run_research_phase(
            store,
            cfg=cfg,
            runner=runner,
            result=result,
            alerter=alerter,
            sleeper=sleeper,
        )
        _run_process_phase(
            store,
            cfg=cfg,
            competition_client=competition_client,
            catalog_client=catalog_client,
            publisher=publisher,
            runner=runner,
            queue=queue,
            result=result,
            sleeper=sleeper,
            day=day,
        )
        _enforce_floor(result, cfg=cfg, alerter=alerter, day=day)
    except Exception as exc:  # pragma: no cover - fatal path
        result.fatal_error = f"{type(exc).__name__}: {exc}"
        alert = alerter(
            f"Daily pipeline fatal error: {exc}",
            level="error",
            context={"error": result.fatal_error},
        )
        result.alerts.append(alert)

    result.published_count = load_daily_publish_state(today=day).published_count
    return result


def _run_discovery_phase(
    store: Any,
    *,
    cfg: DailyPipelineConfig,
    scheduler: AlbumScheduler | None,
    result: DailyPipelineResult,
    alerter: Callable[..., dict[str, Any]],
    sleeper: Callable[[float], None],
    day: date,
) -> None:
    if cfg.skip_discover:
        return

    try:
        active = scheduler or _default_scheduler(cfg)
        sleeper(cfg.stretch_seconds)
        schedule_result = active.run(
            store,
            today=day,
            discover_genius=True,
            refresh_due=True,
            genius_year=None,
            genius_month=None,
            genius_future_months=cfg.genius_future_months,
            genius_limit=cfg.genius_limit,
            persist=not cfg.dry_run,
        )
        genius_failures = list(schedule_result.genius_failures)
        fetch_failures = [
            item
            for item in genius_failures
            if "GeniusPageFetchError" in str(item.get("message", ""))
            or "Cloudflare" in str(item.get("message", ""))
            or "fetch failed" in str(item.get("message", "")).lower()
        ]
        if fetch_failures and schedule_result.summary.discovered_count == 0:
            result.album_discovery_ok = False
            result.album_discovery_error = fetch_failures[0].get("message") or str(
                fetch_failures[0]
            )
            alert = alerter(
                "Album discovery failed after Genius proxy attempts; continuing with research lane.",
                level="warning",
                context={
                    "error": result.album_discovery_error,
                    "genius_failure_count": len(genius_failures),
                },
            )
            result.alerts.append(alert)
        else:
            result.album_discovery_ok = True
    except Exception as exc:
        result.album_discovery_ok = False
        result.album_discovery_error = f"{type(exc).__name__}: {exc}"
        alert = alerter(
            "Album discovery failed after Genius proxy attempts; continuing with research lane.",
            level="warning",
            context={"error": result.album_discovery_error},
        )
        result.alerts.append(alert)


def _run_research_phase(
    store: Any,
    *,
    cfg: DailyPipelineConfig,
    runner: CodexCliRunner | None,
    result: DailyPipelineResult,
    alerter: Callable[..., dict[str, Any]],
    sleeper: Callable[[float], None],
) -> None:
    if cfg.skip_research_gen:
        _import_pending_research(store, result=result)
        return

    active_runner = runner or CodexCliRunner(
        config=CodexCliConfig(timeout_seconds=900, sandbox="read-only")
    )
    try:
        active_runner.ensure_ready()
    except CodexCliError as exc:
        alert = alerter(
            f"Research generation skipped: Codex not ready ({exc})",
            level="warning",
            context={"error": str(exc)},
        )
        result.alerts.append(alert)
        _import_pending_research(store, result=result)
        return

    run_date = berlin_run_date()
    existing_index = load_existing_research_index(store)
    for kind in ("festival", "tour", "moment"):
        try:
            sleeper(cfg.stretch_seconds)
            generated = generate_research_json(
                kind,  # type: ignore[arg-type]
                runner=active_runner,
                run_date=run_date,
                store=store,
                existing_index=existing_index,
            )
            result.research_generation.append(
                {
                    "kind": generated.kind,
                    "run_date": generated.run_date,
                    "output_path": str(generated.output_path),
                    "candidate_count": generated.candidate_count,
                    "dropped_count": generated.dropped_count,
                    "dropped": list(generated.dropped),
                }
            )
            if generated.candidate_count:
                payload = json.loads(generated.output_path.read_text(encoding="utf-8"))
                existing_index = extend_research_index_from_payload(existing_index, payload)
        except Exception as exc:
            result.research_generation_errors.append({"kind": kind, "error": str(exc)})

    _import_pending_research(store, result=result)


def _import_pending_research(store: Any, *, result: DailyPipelineResult) -> None:
    pending_dir = project_root() / "data" / "research" / "pending"
    archive_dir = project_root() / "data" / "research" / "imported"
    pending_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(pending_dir.glob("*.json"))
    if not files:
        result.research_import = {"imported_files": 0, "paths": []}
        return
    summary = import_research_files(
        files,
        store=store,
        archive_dir=str(archive_dir),
    )
    result.research_import = summary.to_dict()


def _run_process_phase(
    store: Any,
    *,
    cfg: DailyPipelineConfig,
    competition_client: Any | None,
    catalog_client: Any | None,
    publisher: SpotifyPublisher | None,
    runner: CodexCliRunner | None,
    queue: AiQueue | None,
    result: DailyPipelineResult,
    sleeper: Callable[[float], None],
    day: date,
) -> None:
    if cfg.skip_publish:
        return

    active_runner = runner or CodexCliRunner()
    active_queue = queue or AiQueue.from_env()
    active_queue.ensure_dirs()
    active_publisher = publisher or SpotifyPublisher.from_env(
        market=cfg.market,
        require_ai_plan=True,
    )
    client = competition_client
    catalog = catalog_client or competition_client
    if client is None or catalog is None:
        from playlist_builder.spotify_album_watcher import SpotifyWebAPIClient

        spotify = SpotifyWebAPIClient.from_env()
        client = client or spotify
        catalog = catalog or spotify

    try:
        active_runner.ensure_ready()
    except CodexCliError as exc:
        result.skipped.append({"reason": f"codex_not_ready: {exc}"})
        return

    # Two passes: first ASAP within cap; second if still under floor.
    for pass_name in ("primary", "floor_backfill"):
        remaining = remaining_daily_capacity(today=day, cap=cfg.daily_cap)
        if remaining <= 0:
            break
        if pass_name == "floor_backfill":
            published = load_daily_publish_state(today=day).published_count
            if published >= cfg.daily_floor:
                break

        backlog = _collect_backlog(store, limit=max(remaining * 5, 50))
        for candidate in backlog:
            if remaining_daily_capacity(today=day, cap=cfg.daily_cap) <= 0:
                break
            outcome = _process_candidate(
                candidate,
                store,
                cfg=cfg,
                client=client,
                catalog=catalog,
                publisher=active_publisher,
                runner=active_runner,
                queue=active_queue,
                sleeper=sleeper,
                day=day,
            )
            if outcome.get("action") == "published":
                result.processed.append(outcome)
            else:
                result.skipped.append(outcome)


def _process_candidate(
    candidate: Any,
    store: Any,
    *,
    cfg: DailyPipelineConfig,
    client: Any,
    catalog: Any,
    publisher: SpotifyPublisher,
    runner: CodexCliRunner,
    queue: AiQueue,
    sleeper: Callable[[float], None],
    day: date,
) -> dict[str, Any]:
    payload = store.get_latest_payload(candidate.dedupe_key) or {}
    lane = "research" if candidate.effective_status == "research_candidate" else "album"

    try:
        if lane == "album":
            evaluation = payload.get("competition_evaluation")
            verdict = (
                str(evaluation.get("competition_verdict") or "").strip().lower()
                if isinstance(evaluation, dict)
                else ""
            )
            if verdict != "publish":
                applied = generate_album_competition_with_retitle(
                    candidate,
                    payload,
                    store,
                    client=client,
                    catalog_client=catalog,
                    runner=runner,
                    queue=queue,
                    market=cfg.market,
                    sleep_fn=sleeper,
                    stretch_seconds=cfg.stretch_seconds,
                )
                if applied.competition_verdict != "publish":
                    return {
                        "dedupe_key": candidate.dedupe_key,
                        "lane": lane,
                        "action": "skipped",
                        "reason": f"competition_{applied.competition_verdict}",
                    }
                payload = store.get_latest_payload(candidate.dedupe_key) or payload
            else:
                # Ensure applied plan exists for publisher.
                from playlist_builder.ai.album_planner import load_applied_plan

                if load_applied_plan(queue, candidate.dedupe_key) is None:
                    applied = generate_album_competition_with_retitle(
                        candidate,
                        payload,
                        store,
                        client=client,
                        catalog_client=catalog,
                        runner=runner,
                        queue=queue,
                        market=cfg.market,
                        sleep_fn=sleeper,
                        stretch_seconds=cfg.stretch_seconds,
                    )
                    if applied.competition_verdict != "publish":
                        return {
                            "dedupe_key": candidate.dedupe_key,
                            "lane": lane,
                            "action": "skipped",
                            "reason": f"competition_{applied.competition_verdict}",
                        }
                    payload = store.get_latest_payload(candidate.dedupe_key) or payload
        else:
            evaluation = payload.get("competition_evaluation")
            verdict = (
                str(evaluation.get("competition_verdict") or "").strip().lower()
                if isinstance(evaluation, dict)
                else ""
            )
            if verdict != "publish":
                applied = generate_research_competition_with_retitle(
                    candidate,
                    payload,
                    store,
                    client=client,
                    runner=runner,
                    market=cfg.market,
                    sleep_fn=sleeper,
                    stretch_seconds=cfg.stretch_seconds,
                )
                if applied.competition_verdict != "publish":
                    return {
                        "dedupe_key": candidate.dedupe_key,
                        "lane": lane,
                        "action": "skipped",
                        "reason": f"competition_{applied.competition_verdict}",
                    }
                payload = store.get_latest_payload(candidate.dedupe_key) or payload

        sleeper(cfg.stretch_seconds)
        publish_result = publisher.publish_candidate(
            candidate,
            payload,
            dry_run=cfg.dry_run,
        )
        if cfg.dry_run:
            return {
                "dedupe_key": candidate.dedupe_key,
                "lane": lane,
                "action": "dry_run",
                "track_count": publish_result.track_count,
                "name": publish_result.plan.name,
                "description": publish_result.plan.description,
            }

        if publish_result.playlist_id and publish_result.playlist_uri:
            store.mark_published(
                candidate.dedupe_key,
                playlist_id=publish_result.playlist_id,
                playlist_uri=publish_result.playlist_uri,
                published_at=datetime.now(timezone.utc),
                publish_slot=publish_result.publish_slot,
            )
            record_daily_publish(candidate.dedupe_key, today=day)
            return {
                "dedupe_key": candidate.dedupe_key,
                "lane": lane,
                "action": "published",
                "playlist_id": publish_result.playlist_id,
                "track_count": publish_result.track_count,
                "name": publish_result.plan.name,
                "description": publish_result.plan.description,
            }
        return {
            "dedupe_key": candidate.dedupe_key,
            "lane": lane,
            "action": "skipped",
            "reason": "publish_returned_no_playlist",
        }
    except MissingResearchCompetitionQueriesError as exc:
        return {
            "dedupe_key": candidate.dedupe_key,
            "lane": lane,
            "action": "skipped",
            "reason": "missing_ai_competition_queries",
            "detail": str(exc),
        }
    except (PlaylistPublishSkipError, PlaylistCompetitionSkipError, ValueError) as exc:
        return {
            "dedupe_key": candidate.dedupe_key,
            "lane": lane,
            "action": "skipped",
            "reason": str(exc),
        }
    except Exception as exc:
        return {
            "dedupe_key": candidate.dedupe_key,
            "lane": lane,
            "action": "skipped",
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _collect_backlog(store: Any, *, limit: int) -> list[Any]:
    """Soonest-release-first among publish-pending album + research candidates."""

    rows = [
        row
        for row in store.list_candidates(limit=max(limit * 3, 100), publish_pending_only=True)
        if row.score_action
        in {
            ScoreAction.READY_TO_PUBLISH.value,
            ScoreAction.QUEUE_FOR_REVIEW.value,
        }
        and (
            row.effective_status == "research_candidate"
            or row.effective_status in ALBUM_STATUSES
        )
    ]

    def sort_key(row: Any) -> tuple[str, str]:
        return (row.event_date or "9999-99-99", row.dedupe_key)

    rows.sort(key=sort_key)
    return rows[:limit]


def _enforce_floor(
    result: DailyPipelineResult,
    *,
    cfg: DailyPipelineConfig,
    alerter: Callable[..., dict[str, Any]],
    day: date,
) -> None:
    published = load_daily_publish_state(today=day).published_count
    result.published_count = published
    if published < cfg.daily_floor:
        alert = alerter(
            f"Daily publish floor missed: published {published} < {cfg.daily_floor}",
            level="warning",
            context={
                "published_count": published,
                "daily_floor": cfg.daily_floor,
                "daily_cap": cfg.daily_cap,
                "album_discovery_ok": result.album_discovery_ok,
            },
        )
        result.alerts.append(alert)


def _default_scheduler(cfg: DailyPipelineConfig) -> AlbumScheduler:
    pre_release_window_days = None
    raw = os.environ.get("PRE_RELEASE_WINDOW_DAYS", "").strip()
    if raw:
        pre_release_window_days = int(raw)
    matcher_config = SpotifyMatcherConfig(
        market=cfg.market,
        pre_release_window_days=pre_release_window_days,
    )
    enricher_config = SpotifyPrereleaseEnrichmentConfig(
        pre_release_window_days=pre_release_window_days,
    )
    return AlbumScheduler.from_env(
        market=cfg.market,
        matcher_config=matcher_config,
        enricher_config=enricher_config,
    )


def env_stretch_seconds() -> float:
    raw = os.environ.get("PIPELINE_STRETCH_SECONDS", "").strip()
    if not raw:
        return 2.0
    return max(0.0, float(raw))


def env_daily_cap() -> int:
    raw = os.environ.get("DAILY_PUBLISH_CAP", "").strip()
    return int(raw) if raw else DEFAULT_DAILY_CAP


def env_daily_floor() -> int:
    raw = os.environ.get("DAILY_PUBLISH_FLOOR", "").strip()
    return int(raw) if raw else DEFAULT_DAILY_FLOOR
