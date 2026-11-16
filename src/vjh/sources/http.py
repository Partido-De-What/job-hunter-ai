"""Polite HTTP client: robots.txt check, per-host delay, limited retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from protego import Protego

from ..models.settings import CollectionSettings


class RobotsDisallowed(Exception):
    """robots.txt forbids the URL, or robots.txt could not be read safely."""


class FetchError(Exception):
    pass


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PoliteClient:
    def __init__(
        self,
        settings: CollectionSettings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, Protego | None] = {}
        self._last_request: dict[str, float] = {}
        self.requests_made = 0
        self._http = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept-Language": settings.accept_language},
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- robots.txt ----------
    def _robots_for(self, url: str) -> Protego | None:
        """Parsed robots.txt, an 'allow all' parser if none exists (4xx),
        or None if it could not be read (5xx / network error) -> do not fetch."""
        key = origin(url)
        if key not in self._robots:
            try:
                resp = self._request(f"{key}/robots.txt")
            except FetchError:
                self._robots[key] = None
            else:
                if resp.status_code == 200:
                    self._robots[key] = Protego.parse(resp.text)
                elif 400 <= resp.status_code < 500:
                    self._robots[key] = Protego.parse("")      # no robots.txt: allowed
                else:
                    self._robots[key] = None
        return self._robots[key]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is not None and rp.can_fetch(url, self.settings.user_agent)

    def robots_status(self, url: str) -> str:
        rp = self._robots_for(url)
        if rp is None:
            return "unreadable (treated as disallowed)"
        return "allowed" if rp.can_fetch(url, self.settings.user_agent) else "disallowed"

    # ---------- requests ----------
    def _wait_turn(self, url: str) -> None:
        key = origin(url)
        last = self._last_request.get(key)
        if last is not None:
            remaining = self.settings.min_delay_seconds - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[key] = self._clock()

    def _request(self, url: str) -> httpx.Response:
        attempt = 0
        while True:
            self._wait_turn(url)
            try:
                self.requests_made += 1
                resp = self._http.get(url)
            except httpx.HTTPError as exc:
                if attempt >= self.settings.max_retries:
                    raise FetchError(f"{url}: {exc.__class__.__name__}: {exc}") from exc
            else:
                if resp.status_code != 429 and resp.status_code < 500:
                    return resp
                if attempt >= self.settings.max_retries:
                    return resp
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    self._sleep(min(int(retry_after), 120))
            attempt += 1
            self._sleep(self.settings.min_delay_seconds * (2 ** attempt))

    def get(self, url: str) -> httpx.Response:
        """GET for automated collection. Raises RobotsDisallowed / FetchError;
        non-2xx answers raise FetchError."""
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt does not allow {url} ({self.robots_status(url)})")
        resp = self._request(url)
        if resp.status_code >= 400:
            raise FetchError(f"{url}: HTTP {resp.status_code}")
        return resp
