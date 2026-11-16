"""Manual import: a URL, pasted text, or a file (.txt .md .html .pdf)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import trafilatura
from pypdf import PdfReader

from ..models.job import Job
from ..models.source import NEVER_AUTOMATIC_DOMAINS
from .base import CollectedJob
from .html_utils import extract_jobpostings, jobposting_to_job, page_title, site_name
from .http import PoliteClient

SOURCE = "Manual import"


class ImportRefused(Exception):
    pass


def _from_html(html: str, *, url: str, now: datetime, title: str | None,
               employer: str | None) -> CollectedJob:
    postings = extract_jobpostings(html)
    if postings:
        job, ext = jobposting_to_job(postings[0], page_url=url, source=SOURCE, now=now,
                                     default_employer=employer)
        updates = {k: v for k, v in (("title", title), ("employer", employer)) if v}
        if updates:
            job = job.model_copy(update=updates)
            job.content_hash = ""
            job = Job.model_validate(job.model_dump())
        return CollectedJob(job=job, external_id=ext)
    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    job = Job(
        title=title or page_title(html) or "(untitled)",
        employer=employer or site_name(html) or (urlsplit(url).netloc or "Unknown"),
        url=url or None, source=SOURCE, discovered_at=now, retrieved_at=now, description=text,
    )
    return CollectedJob(job=job, external_id=None)


def import_url(client: PoliteClient, url: str, *, title: str | None = None,
               employer: str | None = None) -> CollectedJob:
    host = urlsplit(url).netloc.lower()
    if any(d in host for d in NEVER_AUTOMATIC_DOMAINS):
        raise ImportRefused(
            "LinkedIn pages are never fetched. Copy the job text into a .txt file and run:\n"
            '  vjh import-file job.txt --title "..." --employer "..." --url ' + url
        )
    resp = client.get(url)   # robots.txt checked inside
    return _from_html(resp.text, url=str(resp.url), now=datetime.now(), title=title, employer=employer)


def import_text(text: str, *, title: str, employer: str, url: str | None = None) -> CollectedJob:
    if not title or not employer:
        raise ImportRefused("Text import needs --title and --employer.")
    now = datetime.now()
    return CollectedJob(job=Job(title=title, employer=employer, url=url, source=SOURCE,
                                discovered_at=now, retrieved_at=now, description=text.strip()))


def import_file(path: Path, *, title: str | None = None, employer: str | None = None,
                url: str | None = None) -> CollectedJob:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        cj = _from_html(path.read_text(encoding="utf-8", errors="replace"), url=url or "",
                        now=datetime.now(), title=title, employer=employer)
        if url is None:
            cj.job.url = None
        return cj
    if suffix == ".pdf":
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ImportRefused(f"Unsupported file type {suffix}: use .txt, .md, .html or .pdf")
    return import_text(text, title=title or "", employer=employer or "", url=url)
