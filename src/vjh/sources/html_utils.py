"""HTML helpers: schema.org JobPosting extraction and HTML -> text.
All input is untrusted: we only read data out of it, never execute or follow it blindly."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models.common import SalaryPeriod
from ..models.job import Job, Salary

_BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
               "tr", "table", "section", "article", "header", "footer", "blockquote", "pre"]

_UNIT = {"MONTH": SalaryPeriod.MONTHLY, "YEAR": SalaryPeriod.ANNUAL, "HOUR": SalaryPeriod.HOURLY}


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(_BLOCK_TAGS):
        if block.name == "li":
            block.insert_before("\n- ")
        block.append("\n")
    text = soup.get_text("")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _is_jobposting(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(str(x).split("/")[-1] == "JobPosting" for x in types if x)


def extract_jobpostings(html: str) -> list[dict]:
    """All schema.org JobPosting objects in JSON-LD blocks (incl. @graph and lists)."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found.extend(n for n in _walk(data) if _is_jobposting(n))
    return found


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return _text(v.get("name") or v.get("value"))
    if isinstance(v, list):
        parts = [p for p in (_text(x) for x in v) if p]
        return ", ".join(parts) or None
    s = str(v).strip()
    return s or None


def _date(v: Any) -> date | None:
    s = _text(v)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _location(v: Any) -> str | None:
    places = v if isinstance(v, list) else [v]
    out: list[str] = []
    for p in places:
        if not isinstance(p, dict):
            if _text(p):
                out.append(_text(p))
            continue
        addr = p.get("address", p)
        if isinstance(addr, dict):
            parts = [_text(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
            s = ", ".join(dict.fromkeys(x for x in parts if x))
        else:
            s = _text(addr)
        if s:
            out.append(s)
    return "; ".join(dict.fromkeys(out)) or None


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def _salary(v: Any) -> Salary | None:
    if not isinstance(v, dict):
        return None
    val = v.get("value")
    unit = None
    lo = hi = None
    if isinstance(val, dict):
        unit = _UNIT.get(str(val.get("unitText", "")).upper())
        lo = _num(val.get("minValue")) or _num(val.get("value"))
        hi = _num(val.get("maxValue"))
    else:
        lo = _num(val)
        unit = _UNIT.get(str(v.get("unitText", "")).upper())
    source_text = json.dumps(v, ensure_ascii=False)[:500]
    if unit is None or lo is None:
        return Salary(source_text=source_text)          # keep the raw text, invent nothing
    if hi is not None and hi < lo:
        hi = None
    return Salary(amount_min=lo, amount_max=hi, period=unit,
                  currency=_text(v.get("currency")) or "EUR", source_text=source_text)


def jobposting_to_job(jp: dict, *, page_url: str, source: str, now: datetime,
                      default_employer: str | None = None) -> tuple[Job, str | None]:
    url = _text(jp.get("url"))
    url = urljoin(page_url, url) if url else page_url
    ident = jp.get("identifier")
    external_id = _text(ident.get("value") if isinstance(ident, dict) else ident)
    job = Job(
        title=_text(jp.get("title")) or "(untitled)",
        employer=_text(jp.get("hiringOrganization")) or default_employer or "Unknown",
        location=_location(jp.get("jobLocation")),
        url=url,
        source=source,
        published_at=_date(jp.get("datePosted")),
        deadline=_date(jp.get("validThrough")),
        discovered_at=now,
        retrieved_at=now,
        salary=_salary(jp.get("baseSalary")),
        employment_type=_text(jp.get("employmentType")),
        description=html_to_text(str(jp.get("description") or "")),
    )
    return job, external_id


def page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    return soup.title.get_text(strip=True) if soup.title else None


def site_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:site_name")
    return og["content"].strip() if og and og.get("content") else None
