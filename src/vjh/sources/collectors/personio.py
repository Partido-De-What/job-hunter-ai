"""Official public Personio XML feed: https://{company}.jobs.personio.de/xml"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register
from ..html_utils import html_to_text
from ..http import origin


def feed_url(src: SourceConfig) -> str:
    if src.params.get("feed_url"):
        return src.params["feed_url"]
    company = src.params["company"]
    lang = src.params.get("language", "de")
    return f"https://{company}.jobs.personio.de/xml?language={lang}"


def _t(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text and child.text.strip() else None


def parse_feed(xml_text: str, *, base: str, src: SourceConfig, now: datetime) -> list[CollectedJob]:
    root = ET.fromstring(xml_text)
    employer = src.params.get("employer", src.name)
    only = [s.lower() for s in src.params.get("only_locations", [])]
    out: list[CollectedJob] = []
    for pos in root.iter("position"):
        pid = _t(pos, "id")
        offices = [o for o in [_t(pos, "office")] if o]
        add = pos.find("additionalOffices")
        if add is not None:
            offices += [o.text.strip() for o in add.findall("office") if o.text]
        location = ", ".join(dict.fromkeys(offices)) or None
        if only and not any(k in (location or "").lower() for k in only):
            continue
        sections = []
        for jd in pos.iter("jobDescription"):
            name, value = _t(jd, "name"), _t(jd, "value")
            if value:
                sections.append(f"{name}\n{html_to_text(value)}" if name else html_to_text(value))
        created = _t(pos, "createdAt")
        emp_type = ", ".join(x for x in (_t(pos, "employmentType"), _t(pos, "schedule")) if x) or None
        out.append(CollectedJob(
            job=Job(
                title=_t(pos, "name") or "(untitled)",
                employer=_t(pos, "subcompany") or employer,
                location=location,
                url=f"{base}/job/{pid}" if pid else None,
                source=src.name,
                published_at=datetime.fromisoformat(created).date() if created else None,
                discovered_at=now, retrieved_at=now,
                employment_type=emp_type,
                description="\n\n".join(sections),
            ),
            external_id=pid,
        ))
    return out


@register("personio")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    url = feed_url(src)
    resp = ctx.client.get(url)
    return parse_feed(resp.text, base=origin(str(resp.url)), src=src, now=ctx.now)
