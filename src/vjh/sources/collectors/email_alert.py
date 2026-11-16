"""Saved job-alert emails (.eml) in data/imports/alerts.

Each job link in an alert becomes a job stub (title = link text). Links are NOT opened,
so this also works for LinkedIn alerts. Processed files move to alerts/processed/.
"""

from __future__ import annotations

import email
import re
import shutil
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ...models.job import Job
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register

BOARDS = {
    "karriere.at": "karriere.at",
    "stepstone": "StepStone Austria",
    "ams.at": "AMS alle jobs",
    "indeed": "Indeed Austria",
    "linkedin": "LinkedIn Jobs",
    "euraxess": "EURAXESS",
    "academicpositions": "Academic Positions",
}
JOB_LINK = re.compile(r"job|stelle|vacanc|position|karriere|career|posting|offer|/view/", re.I)
NOT_A_JOB = re.compile(
    r"unsubscribe|abmelden|abbestellen|einstellungen|settings|preferences|privacy|datenschutz|"
    r"impressum|alle jobs|all jobs|view all|mehr jobs|more jobs|help|hilfe|login|anmelden|app store|google play",
    re.I,
)


def _board(msg: EmailMessage, links: list[str]) -> str | None:
    hay = " ".join([str(msg.get("From", ""))] + links[:5]).lower()
    for key, name in BOARDS.items():
        if key in hay:
            return name
    return None


def _links(msg: EmailMessage) -> list[tuple[str, str]]:
    html_part = msg.get_body(preferencelist=("html",))
    pairs: list[tuple[str, str]] = []
    if html_part is not None:
        soup = BeautifulSoup(html_part.get_content(), "html.parser")
        for a in soup.find_all("a", href=True):
            pairs.append((a.get_text(" ", strip=True), a["href"].strip()))
    else:
        text_part = msg.get_body(preferencelist=("plain",))
        if text_part is not None:
            prev = ""
            for line in text_part.get_content().splitlines():
                for url in re.findall(r"https?://\S+", line):
                    title = line.replace(url, "").strip(" -:|") or prev
                    pairs.append((title, url.rstrip(").,>")))
                if line.strip():
                    prev = line.strip()
    return pairs


def parse_alert(raw: bytes, *, fallback_source: str, now) -> list[CollectedJob]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    pairs = _links(msg)
    board = _board(msg, [u for _, u in pairs]) or fallback_source
    subject = str(msg.get("Subject", "")).strip()
    try:
        received = parsedate_to_datetime(msg["Date"]).date() if msg["Date"] else None
    except (TypeError, ValueError):
        received = None
    out: list[CollectedJob] = []
    seen: set[str] = set()
    for title, url in pairs:
        title = re.sub(r"\s+", " ", title).strip()
        if not url.startswith("http") or url in seen:
            continue
        if not (8 <= len(title) <= 150) or NOT_A_JOB.search(title) or NOT_A_JOB.search(url):
            continue
        if not JOB_LINK.search(url):
            continue
        seen.add(url)
        out.append(CollectedJob(
            job=Job(
                title=title,
                employer="Unknown (from email alert)",
                url=url,
                source=board,
                published_at=received,
                discovered_at=now, retrieved_at=now,
                description=f"Job alert email: '{subject}' ({urlsplit(url).netloc}). "
                            "Only the title is known; import the full ad to evaluate it.",
            ),
            external_id=url,
        ))
    return out


@register("email_alert")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    folder = Path(src.params.get("folder") or ctx.data_dir / "imports" / "alerts")
    folder.mkdir(parents=True, exist_ok=True)
    done = folder / "processed"
    out: list[CollectedJob] = []
    for path in sorted(folder.glob("*.eml")):
        jobs = parse_alert(path.read_bytes(), fallback_source=src.name, now=ctx.now)
        ctx.log.info("%s: %s -> %d job links", src.name, path.name, len(jobs))
        out.extend(jobs)
        done.mkdir(exist_ok=True)
        shutil.move(str(path), done / path.name)
    return out
