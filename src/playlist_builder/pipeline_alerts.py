"""Pipeline alerts: Telegram Bot API + local log file."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from playlist_builder.persistence.env import project_root


class AlertError(RuntimeError):
    """Raised when Telegram delivery fails (log write still attempted)."""


def alerts_log_path() -> Path:
    return project_root() / "logs" / "pipeline-alerts.log"


def send_pipeline_alert(
    message: str,
    *,
    level: str = "warning",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append to local alert log and optionally send Telegram.

    Requires ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` for Telegram.
    Missing Telegram config is not fatal — log-only still succeeds.
    """

    stamped = datetime.now(timezone.utc).isoformat()
    payload = {
        "at": stamped,
        "level": level,
        "message": message.strip(),
        "context": context or {},
    }
    log_path = alerts_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    telegram = _send_telegram(_format_telegram(payload))
    return {"logged": True, "log_path": str(log_path), "telegram": telegram}


def _format_telegram(payload: dict[str, Any]) -> str:
    lines = [
        f"[playlist-builder] {payload['level'].upper()}",
        str(payload["message"]),
        f"at {payload['at']}",
    ]
    context = payload.get("context") or {}
    if context:
        lines.append(json.dumps(context, ensure_ascii=True))
    return "\n".join(lines)


def _send_telegram(text: str) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"sent": False, "reason": "missing_TELEGRAM_BOT_TOKEN_or_TELEGRAM_CHAT_ID"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode(
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = Request(url, data=body, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        decoded = json.loads(raw) if raw.strip() else {}
        ok = bool(decoded.get("ok")) if isinstance(decoded, dict) else False
        return {"sent": ok, "response": decoded if isinstance(decoded, dict) else {}}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}
