"""Job pages that link one PDF per vacancy (e.g. BOKU Mitteilungsblatt notices).

params:
  link_pattern: regex for the PDF links (required)
  employer:     employer name
  location:     default location
Title = link text; description = text extracted from the PDF.
"""

from __future__ import annotations

import io
import re
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register


def pdf_links(html: str, base: str, pattern: str) -> list[tuple[str, str]]:
    rx = re.compile(pattern, re.I)
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(base, a["href"]))[0]
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if rx.search(url) and title and url not in out:
            out[url] = title
    return [(t, u) for u, t in out.items()]


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


@register("pdf_links")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    resp = ctx.client.get(src.params.get("listing_url", src.url))
    links = pdf_links(resp.text, str(resp.url), src.params["link_pattern"])
    new = [(t, u) for t, u in links if u not in ctx.known_urls]
    ctx.skipped_known += len(links) - len(new)
    out: list[CollectedJob] = []
    for title, url in new[: ctx.settings.max_pages_per_source]:
        try:
            text = pdf_text(ctx.client.get(url).content)
        except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the source
            ctx.log.warning("%s: skipped %s (%s)", src.name, url, exc)
            continue
        out.append(CollectedJob(
            job=Job(title=title, employer=src.params.get("employer", src.name),
                    location=src.params.get("location"), url=url, source=src.name,
                    discovered_at=ctx.now, retrieved_at=ctx.now, description=text),
            external_id=url,
        ))
    return out
