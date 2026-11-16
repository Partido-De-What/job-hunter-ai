"""A fake internet for tests: maps URLs to responses and records every request."""

from __future__ import annotations

import httpx

from vjh.models.settings import CollectionSettings
from vjh.sources.http import PoliteClient


class FakeWeb:
    def __init__(self, pages: dict[str, object] | None = None):
        # value: str (200 text/html), bytes, or (status, body, headers)
        self.pages = dict(pages or {})
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url not in self.pages:
            if url.endswith("/robots.txt"):
                return httpx.Response(404, text="")
            return httpx.Response(404, text="not found")
        value = self.pages[url]
        if callable(value):
            value = value()
        if isinstance(value, tuple):
            status, body, headers = value
            return httpx.Response(status, content=body.encode() if isinstance(body, str) else body,
                                  headers=headers)
        body = value.encode() if isinstance(value, str) else value
        return httpx.Response(200, content=body)

    def client(self, **settings) -> tuple[PoliteClient, list[float]]:
        sleeps: list[float] = []
        clock = iter(range(0, 10_000))
        cfg = CollectionSettings(**{"min_delay_seconds": 1.0, **settings})
        client = PoliteClient(cfg, transport=httpx.MockTransport(self.handler),
                              sleep=sleeps.append, clock=lambda: float(next(clock)))
        return client, sleeps

    def page_requests(self) -> list[str]:
        return [u for u in self.requests if not u.endswith("/robots.txt")]
