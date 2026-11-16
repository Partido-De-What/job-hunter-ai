"""Official SmartRecruiters Posting API (public postings, no login).
List: https://api.smartrecruiters.com/v1/companies/{company}/postings"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register
from ..html_utils import html_to_text

API = "https://api.smartrecruiters.com/v1/companies"


def _date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def posting_to_job(item: dict, detail: dict | None, *, src: SourceConfig, now: datetime) -> CollectedJob:
    loc = item.get("location") or {}
    location = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x) or None
    description = ""
    url = None
    if detail:
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
            sec = sections.get(key) or {}
            if sec.get("text"):
                parts.append(f"{sec.get('title') or key}\n{html_to_text(sec['text'])}")
        description = "\n\n".join(parts)
        url = detail.get("postingUrl") or detail.get("applyUrl")
    company = src.params["company"]
    return CollectedJob(
        job=Job(
            title=item.get("name") or "(untitled)",
            employer=(item.get("company") or {}).get("name") or src.params.get("employer", src.name),
            location=location,
            url=url or f"https://jobs.smartrecruiters.com/{company}/{item.get('id')}",
            source=src.name,
            published_at=_date(item.get("releasedDate")),
            discovered_at=now, retrieved_at=now,
            employment_type=(item.get("typeOfEmployment") or {}).get("label"),
            description=description,
        ),
        external_id=str(item.get("id")) if item.get("id") else None,
    )


@register("smartrecruiters")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    company = src.params["company"]
    query = {"limit": 100}
    if src.params.get("country"):
        query["country"] = src.params["country"]
    items: list[dict] = []
    offset = 0
    while True:
        page = ctx.client.get(f"{API}/{company}/postings?{urlencode({**query, 'offset': offset})}").json()
        content = page.get("content") or []
        items.extend(content)
        offset += len(content)
        if not content or offset >= int(page.get("totalFound", 0)) or offset >= 1000:
            break
    out: list[CollectedJob] = []
    details_left = ctx.settings.max_pages_per_source
    for item in items:
        detail = None
        if details_left > 0 and item.get("ref"):
            detail = ctx.client.get(item["ref"]).json()
            details_left -= 1
        out.append(posting_to_job(item, detail, src=src, now=ctx.now))
    return out
