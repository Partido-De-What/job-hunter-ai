"""`vjh sources check`: per-source robots.txt check and feed / ATS detection.
Read-only: it reports findings, it never switches a source to automatic by itself."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from ..models.source import NEVER_AUTOMATIC_DOMAINS, SourceConfig
from .html_utils import extract_jobpostings
from .http import FetchError, PoliteClient

ATS_PATTERNS = {
    "personio": r"[\w-]+\.jobs\.personio\.(?:de|com)",
    "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com/[\w-]+",
    "greenhouse": r"boards\.greenhouse\.io/[\w-]+",
    "lever": r"jobs\.lever\.co/[\w-]+",
    "workday": r"[\w-]+\.wd\d+\.myworkdayjobs\.com[\w/-]*",
    "successfactors": r"[\w.-]*(?:successfactors|sapsf)\.(?:com|eu)[\w/.-]*",
    "taleo": r"[\w.-]+\.taleo\.net[\w/.-]*",
    "prescreen": r"[\w.-]*prescreen\.io[\w/.-]*",
    "softgarden": r"[\w.-]+\.softgarden\.io",
    "dvinci": r"[\w.-]+\.dvinci(?:-easy)?\.(?:de|com)",
    "rexx": r"[\w.-]*rexx-systems\.com[\w/.-]*",
    "jobbase": r"[\w.-]*jobbase\.io[\w/.-]*",
    "umantis": r"[\w.-]*umantis\.com[\w/.-]*",
    "join": r"join\.com/companies/[\w-]+",
}


@dataclass
class CheckResult:
    name: str
    url: str
    robots: str = "-"
    http: str = "-"
    final_url: str | None = None
    feeds: list[str] = field(default_factory=list)
    jobpostings: int = 0
    ats: dict[str, str] = field(default_factory=dict)
    note: str = ""


def analyse_html(html: str, base: str) -> tuple[list[str], int, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    feeds = []
    for link in soup.find_all("link", attrs={"type": re.compile(r"rss|atom", re.I)}):
        if link.get("href"):
            from urllib.parse import urljoin
            feeds.append(urljoin(base, link["href"]))
    ats = {}
    for name, pattern in ATS_PATTERNS.items():
        m = re.search(pattern, html, re.I)
        if m:
            ats[name] = m.group(0)
    return feeds, len(extract_jobpostings(html)), ats


def check_source(client: PoliteClient, src: SourceConfig) -> CheckResult:
    res = CheckResult(name=src.name, url=src.url)
    if not src.url.startswith("http"):
        res.note = "local source"
        return res
    if any(d in src.url for d in NEVER_AUTOMATIC_DOMAINS):
        res.note = "never fetched by policy (alerts / manual only)"
        return res
    res.robots = client.robots_status(src.url)
    if res.robots != "allowed":
        res.note = "robots.txt does not permit automated access to this page"
        return res
    try:
        resp = client.get(src.url)
    except FetchError as exc:
        res.http = "error"
        res.note = str(exc)
        return res
    res.http = str(resp.status_code)
    res.final_url = str(resp.url)
    res.feeds, res.jobpostings, res.ats = analyse_html(resp.text, str(resp.url))
    return res


def write_report(results: list[CheckResult], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"source-check-{datetime.now():%Y%m%d-%H%M%S}.md"
    lines = ["# Source check", "", f"Run: {datetime.now():%Y-%m-%d %H:%M}", "",
             "| Source | robots.txt | HTTP | JobPosting data | Feeds | ATS detected | Note |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.name} | {r.robots} | {r.http} | {r.jobpostings} | "
                     f"{'<br>'.join(r.feeds) or '-'} | "
                     f"{'<br>'.join(f'{k}: {v}' for k, v in r.ats.items()) or '-'} | "
                     f"{r.note or (r.final_url if r.final_url != r.url else '')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
