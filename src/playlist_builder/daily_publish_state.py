"""Daily publish cap / floor state for the unified pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from playlist_builder.persistence.env import project_root

DEFAULT_DAILY_CAP = 20
DEFAULT_DAILY_FLOOR = 5


@dataclass(frozen=True, slots=True)
class DailyPublishState:
    day: str
    published_count: int
    published_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "published_count": self.published_count,
            "published_keys": list(self.published_keys),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def daily_publish_state_path() -> Path:
    return project_root() / "data" / "daily_publish_state.json"


def load_daily_publish_state(*, today: date | None = None) -> DailyPublishState:
    day = (today or date.today()).isoformat()
    path = daily_publish_state_path()
    if not path.is_file():
        return DailyPublishState(day=day, published_count=0, published_keys=())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DailyPublishState(day=day, published_count=0, published_keys=())
    if not isinstance(payload, dict) or str(payload.get("day", "")) != day:
        return DailyPublishState(day=day, published_count=0, published_keys=())
    keys = payload.get("published_keys")
    if not isinstance(keys, list):
        keys = []
    cleaned = tuple(str(item) for item in keys if str(item).strip())
    count = int(payload.get("published_count", len(cleaned)) or 0)
    return DailyPublishState(day=day, published_count=count, published_keys=cleaned)


def record_daily_publish(dedupe_key: str, *, today: date | None = None) -> DailyPublishState:
    state = load_daily_publish_state(today=today)
    if dedupe_key in state.published_keys:
        return state
    updated = DailyPublishState(
        day=state.day,
        published_count=state.published_count + 1,
        published_keys=state.published_keys + (dedupe_key,),
    )
    path = daily_publish_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated.to_dict(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return updated


def remaining_daily_capacity(
    *,
    today: date | None = None,
    cap: int = DEFAULT_DAILY_CAP,
) -> int:
    state = load_daily_publish_state(today=today)
    return max(0, cap - state.published_count)
