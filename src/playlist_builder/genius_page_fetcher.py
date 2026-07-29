"""Fetch Genius calendar pages via Scrapling (VPS/datacenter IP friendly)."""

from __future__ import annotations

import os
from collections.abc import Callable
from urllib.request import Request, urlopen

from playlist_builder.spotify_ui_scraper import extract_html


class GeniusPageFetchError(RuntimeError):
    """Raised when all Genius page fetch strategies fail."""


DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
}

CLOUDFLARE_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "just a moment",
    "verify you are human",
    "attention required",
    "cloudflare",
)


def _ensure_calendar_html(html: str) -> None:
    lowered = html.lower()
    if 'data-lyrics-container="true"' in html:
        return
    if any(marker in lowered for marker in CLOUDFLARE_MARKERS):
        raise GeniusPageFetchError(
            "Genius blocked by Cloudflare captcha/challenge (datacenter IP). "
            "Set GENIUS_FETCH_PROXY_1..5 with residential proxies, or run album "
            "discovery on Windows and sync the DB."
        )
    raise GeniusPageFetchError(
        "Genius response missing calendar lyrics container (blocked or wrong page)"
    )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def genius_proxy_slots() -> list[str | None]:
    """Return configured Genius proxy URLs (up to 5), falling back to GENIUS_FETCH_PROXY.

    A trailing ``None`` means "try once without proxy" when no slots are configured.
    """

    slots: list[str] = []
    for index in range(1, 6):
        value = os.environ.get(f"GENIUS_FETCH_PROXY_{index}", "").strip()
        if value:
            slots.append(value)
    legacy = os.environ.get("GENIUS_FETCH_PROXY", "").strip()
    if legacy and legacy not in slots:
        slots.insert(0, legacy)
    if slots:
        return list(slots)
    return [None]


def _proxy_kwargs(proxy: str | None = None) -> dict[str, str]:
    if proxy is None:
        proxy = os.environ.get("GENIUS_FETCH_PROXY", "").strip() or None
    if not proxy:
        return {}
    return {"proxy": proxy}


def fetch_genius_page_html(
    url: str,
    *,
    headless: bool | None = None,
    timeout_ms: int | None = None,
) -> tuple[str, str]:
    """Return ``(html, fetcher_name)``. Escalates through Scrapling fetchers and proxies."""
    if headless is None:
        headless = _env_flag("GENIUS_FETCH_HEADLESS", default=True)
    if timeout_ms is None:
        timeout_ms = int(os.environ.get("GENIUS_FETCH_TIMEOUT_MS", "30000"))

    errors: list[str] = []
    for proxy_index, proxy in enumerate(genius_proxy_slots(), start=1):
        proxy_label = f"proxy{proxy_index}" if proxy else "direct"
        for fetcher_name, fetch in _fetch_strategies(
            url,
            headless=headless,
            timeout_ms=timeout_ms,
            proxy=proxy,
        ):
            try:
                html = fetch()
                _ensure_calendar_html(html)
                return html, f"{fetcher_name}/{proxy_label}"
            except Exception as exc:  # pragma: no cover - exercised via integration
                errors.append(
                    f"{fetcher_name}/{proxy_label}: {type(exc).__name__}: {exc}"
                )

    raise GeniusPageFetchError("Genius page fetch failed: " + "; ".join(errors))


def _fetch_strategies(
    url: str,
    *,
    headless: bool,
    timeout_ms: int,
    proxy: str | None = None,
) -> list[tuple[str, Callable[[], str]]]:
    mode = os.environ.get("GENIUS_FETCH_MODE", "auto").strip().lower()
    solve_cloudflare = _env_flag("GENIUS_FETCH_SOLVE_CLOUDFLARE", default=True)
    stealth_timeout = max(timeout_ms, 60_000) if solve_cloudflare else timeout_ms

    stealthy = (
        "StealthyFetcher",
        lambda: _fetch_with_stealthy_fetcher(
            url,
            headless=headless,
            timeout_ms=stealth_timeout,
            solve_cloudflare=solve_cloudflare,
            proxy=proxy,
        ),
    )
    dynamic = (
        "DynamicFetcher",
        lambda: _fetch_with_dynamic_fetcher(
            url,
            headless=headless,
            timeout_ms=stealth_timeout,
            proxy=proxy,
        ),
    )
    http = (
        "Fetcher",
        lambda: _fetch_with_fetcher(url, timeout_ms=timeout_ms, proxy=proxy),
    )
    legacy = (
        "urllib",
        lambda: _fetch_with_urllib(url, timeout_ms=timeout_ms),
    )

    if mode == "stealth":
        return [stealthy, dynamic]
    if mode == "http":
        return [http, legacy]
    if solve_cloudflare:
        # Datacenter IPs: skip lightweight HTTP first — it often triggers CF walls.
        return [stealthy, dynamic, http, legacy]
    return [http, stealthy, dynamic, legacy]


def _fetch_with_fetcher(url: str, *, timeout_ms: int, proxy: str | None = None) -> str:
    from scrapling.fetchers import Fetcher

    page = Fetcher.get(
        url,
        headers=dict(DEFAULT_BROWSER_HEADERS),
        timeout=timeout_ms / 1000,
        stealthy_headers=True,
        impersonate="chrome",
        **_proxy_kwargs(proxy),
    )
    return extract_html(page)


def _fetch_with_stealthy_fetcher(
    url: str,
    *,
    headless: bool,
    timeout_ms: int,
    solve_cloudflare: bool,
    proxy: str | None = None,
) -> str:
    from scrapling.fetchers import StealthyFetcher

    page = StealthyFetcher.fetch(
        url,
        headless=headless,
        network_idle=True,
        timeout=timeout_ms,
        wait_selector='[data-lyrics-container="true"]',
        wait_selector_state="attached",
        google_search=True,
        disable_resources=not solve_cloudflare,
        solve_cloudflare=solve_cloudflare,
        block_webrtc=True,
        hide_canvas=True,
        retries=2,
        **_proxy_kwargs(proxy),
    )
    return extract_html(page)


def _fetch_with_dynamic_fetcher(
    url: str,
    *,
    headless: bool,
    timeout_ms: int,
    proxy: str | None = None,
) -> str:
    from scrapling.fetchers import DynamicFetcher

    page = DynamicFetcher.fetch(
        url,
        headless=headless,
        network_idle=True,
        timeout=timeout_ms,
        wait_selector='[data-lyrics-container="true"]',
        wait_selector_state="attached",
        google_search=True,
        **_proxy_kwargs(proxy),
    )
    return extract_html(page)


def _fetch_with_urllib(url: str, *, timeout_ms: int) -> str:
    request = Request(
        url,
        headers=dict(DEFAULT_BROWSER_HEADERS),
        method="GET",
    )
    with urlopen(request, timeout=timeout_ms / 1000) as response:
        return response.read().decode("utf-8", errors="replace")
