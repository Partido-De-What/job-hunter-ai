"""Career pages on permitted sites (robots.txt checked for every request).

Prefers schema.org JobPosting data. With `text_fallback: true`, a detail page without
it becomes a job from its heading + main text (trafilatura).

params:
  listing_url:   page listing the jobs (default: source url)
  link_pattern:  regex for links to job detail pages (optional)
  employer:      employer name if the data lacks one
  location:      default location for text-fallback jobs
  text_fallback: true/false (default false)
"""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin

import trafilatura
from bs4 import BeautifulSoup

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register
from ..html_utils import extract_jobpostings, jobposting_to_job, page_title


def detail_links(html: str, base: str, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(base, a["href"]))[0]
        if url.startswith("http") and rx.search(url) and url.rstrip("/") != base.rstrip("/") and url not in links:
            links.append(url)
    return links


MIN_DESCRIPTION = 500   # shorter structured descriptions are usually just a teaser


def clean_title(title: str) -> str:
    """'Job title | Site name' -> 'Job title'."""
    parts = [p.strip() for p in title.split(" | ")]
    return parts[0] if len(parts) > 1 and parts[0] else title.strip()


def jobs_from_page(html: str, page_url: str, src: SourceConfig, ctx: CollectContext,
                   *, detail: bool) -> list[CollectedJob]:
    employer = src.params.get("employer", src.name)
    out = []
    page_text: str | None = None
    for jp in extract_jobpostings(html):
        job, ext = jobposting_to_job(jp, page_url=page_url, source=src.name, now=ctx.now,
                                     default_employer=employer)
        if detail and len(job.description) < MIN_DESCRIPTION:
            page_text = page_text if page_text is not None else (
                trafilatura.extract(html, include_comments=False, include_tables=True) or "")
            if len(page_text) > len(job.description):
                job = Job.model_validate({**job.model_dump(), "description": page_text, "content_hash": ""})
        out.append(CollectedJob(job=job, external_id=ext or job.url))
    if not out and detail and src.params.get("text_fallback"):
        title = clean_title(page_title(html) or "")
        text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
        if title and len(text) >= 200:
            out.append(CollectedJob(
                job=Job(title=title, employer=employer, location=src.params.get("location"),
                        url=page_url, source=src.name, discovered_at=ctx.now,
                        retrieved_at=ctx.now, description=text),
                external_id=page_url,
            ))
        else:
            ctx.log.warning("%s: no usable job text on %s", src.name, page_url)
    return out


@register("jsonld_page")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    listing = src.params.get("listing_url", src.url)
    resp = ctx.client.get(listing)
    out = jobs_from_page(resp.text, str(resp.url), src, ctx, detail=False)
    pattern = src.params.get("link_pattern")
    if pattern:
        all_links = detail_links(resp.text, str(resp.url), pattern)
        links = [u for u in all_links if u not in ctx.known_urls]
        ctx.skipped_known += len(all_links) - len(links)
        for url in links[: ctx.settings.max_pages_per_source]:
            try:
                page = ctx.client.get(url)
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the source
                ctx.log.warning("%s: skipped %s (%s)", src.name, url, exc)
                continue
            out.extend(jobs_from_page(page.text, str(page.url), src, ctx, detail=True))
    return out
