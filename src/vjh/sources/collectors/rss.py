"""Public RSS / Atom job feeds."""

from __future__ import annotations

from datetime import date

import feedparser

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register
from ..html_utils import html_to_text


def parse_feed(content: bytes, *, src: SourceConfig, now) -> list[CollectedJob]:
    feed = feedparser.parse(content)
    out: list[CollectedJob] = []
    for e in feed.entries:
        published = e.get("published_parsed") or e.get("updated_parsed")
        out.append(CollectedJob(
            job=Job(
                title=(e.get("title") or "(untitled)").strip(),
                employer=src.params.get("employer") or (e.get("author") or "").strip() or src.name,
                location=src.params.get("default_location"),
                url=e.get("link"),
                source=src.name,
                published_at=date(*published[:3]) if published else None,
                discovered_at=now, retrieved_at=now,
                description=html_to_text(e.get("summary") or ""),
            ),
            external_id=e.get("id") or e.get("link"),
        ))
    return out


@register("rss")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    resp = ctx.client.get(src.params.get("feed_url", src.url))
    return parse_feed(resp.content, src=src, now=ctx.now)
