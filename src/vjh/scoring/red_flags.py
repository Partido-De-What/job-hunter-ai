"""Warning signs on a job ad ("ghost job" and quality signals). Free, local, no AI.

Signals are hints, never proof: an old or reposted ad can still be real. Based on the warning
signs used by ghost-job studies and checkers (age of the ad, repeated reposting, missing pay,
very little information, anonymous agency postings) plus one Austrian rule: since 2011 the
Equal Treatment Act (GlBG §9 Abs. 2) requires job ads in Austria to state the minimum pay.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.tables import JobRow

OLD_AD_DAYS = 30           # ads open longer than this are often filled or "evergreen"
REPOST_GAP_DAYS = 21       # the same job seen again after this many days counts as a repost
SHORT_AD_CHARS = 400
# agency words in the employer name, or "for our client" wording in the ad
_AGENCY_NAME = re.compile(r"(?i)(personalberatung|personaldienstleist|personalservice|staffing|recruit|"
                          r"arbeitskräfteüberlass|confidential|anonym|vertraulich|\bpersonal\b)")
_AGENCY_AD = re.compile(r"(?i)\b(unser (?:kunde|klient)\b|unsere (?:kundin|klientin)\b|"
                        r"im auftrag unsere[rs]? (?:kunden|kundin|klienten)\b|our client\b|on behalf of (?:our|a) client)")
# pay mentioned in the ad text (the salary field is often empty even when the ad states pay)
_PAY = re.compile(r"(?i)(€|\beur\b|euro|brutto|gross|gehalt|entlohnung|bezahlung|vergütung|salary|"
                  r"kollektivvertrag|\bkv\b|überzahlung|mindestlohn|stipend|per (?:month|annum|year))")
_GENDER = re.compile(r"\([^)]*\)|\b[mwfdx]\s*/\s*[mwfdx](?:\s*/\s*[mwfdx])?\b|[*:_](?:in|innen)\b", re.I)


@dataclass
class Flag:
    level: str          # "warn" (worth a look) or "info"
    text: str


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", _GENDER.sub(" ", text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _austrian(job: JobRow) -> bool:
    loc = (job.location or "").lower()
    return any(k in loc for k in ("austria", "österreich", "osterreich", "wien", "vienna", "niederösterreich",
                                  "lower austria", "graz", "linz", "salzburg", "innsbruck", "klosterneuburg",
                                  "st. pölten", "tulln", "krems", "wiener neustadt", "mödling", "baden"))


def earlier_sightings(session: Session, job: JobRow) -> list[JobRow]:
    """Other ads with the same employer and title found at least REPOST_GAP_DAYS earlier."""
    title, employer = _norm(job.title), _norm(job.employer)
    if not title or not employer:
        return []
    cutoff = job.discovered_at - timedelta(days=REPOST_GAP_DAYS)
    rows = session.scalars(select(JobRow).where(JobRow.id != job.id, JobRow.discovered_at <= cutoff))
    return [r for r in rows if _norm(r.title) == title and _norm(r.employer) == employer]


def job_flags(session: Session, job: JobRow, today: date | None = None) -> list[Flag]:
    today = today or date.today()
    flags: list[Flag] = []
    if job.published_at and (today - job.published_at).days > OLD_AD_DAYS:
        flags.append(Flag("warn", f"Posted {(today - job.published_at).days} days ago: older ads are often "
                                  "already filled. Check that it is still open before you invest time."))
    seen = earlier_sightings(session, job)
    if seen:
        first = min(r.discovered_at for r in seen)
        flags.append(Flag("warn", f"Seen before: the same title from this employer was first found on "
                                  f"{first:%d.%m.%Y} ({len(seen)} earlier ad(s)). Often reposted ads can be "
                                  "hard to fill, or kept open without a real vacancy."))
    if job.deadline and job.deadline < today:
        flags.append(Flag("warn", f"The application deadline ({job.deadline:%d.%m.%Y}) has passed."))
    if not job.salary and _austrian(job) and not _PAY.search(job.description or ""):
        flags.append(Flag("info", "No pay stated, although Austrian job ads must give the minimum salary "
                                  "(GlBG §9). Ask about pay early."))
    if len((job.description or "").strip()) < SHORT_AD_CHARS:
        flags.append(Flag("info", "Very short ad: little information about tasks or requirements."))
    if _AGENCY_NAME.search(job.employer or "") or _AGENCY_AD.search((job.description or "")[:2000]):
        flags.append(Flag("info", "Looks like a recruiting agency or an unnamed employer: ask who the "
                                  "employer is before sending documents."))
    return flags
