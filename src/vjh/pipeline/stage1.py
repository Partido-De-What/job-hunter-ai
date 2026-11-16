"""Stage 1: deterministic Python filtering and cross-source deduplication.

Nothing is deleted. Each job gets a status and, when set aside, a human-readable reason,
so unusual jobs can still be reviewed (spec Part 7: no rejection without human review).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.tables import JobRow
from ..models.common import JobStatus
from ..models.job import Salary

MIN_TEXT_CHARS = 300
@dataclass
class Rules:
    title_patterns: list[re.Pattern]
    topic_patterns: list[re.Pattern]
    exclude_title_patterns: list[re.Pattern]
    min_topic_matches: int
    max_age_days: int
    min_salary: float
    today: date = field(default_factory=date.today)
    local_places: re.Pattern | None = None       # settings: candidate.local_place_patterns
    foreign_places: re.Pattern | None = None     # settings: candidate.foreign_place_patterns
    region: str = "the target area"

    @classmethod
    def from_config(cls, search_terms: dict, *, max_age_days: int, min_salary: float,
                    today: date | None = None, local_places: list[str] | None = None,
                    foreign_places: list[str] | None = None, region: str = "the target area") -> Rules:
        rel = search_terms.get("relevance", {})
        comp = lambda pats: [re.compile(r"(?<![\w])" + p, re.I) for p in pats]  # noqa: E731
        return cls(
            title_patterns=comp(rel.get("title_patterns", [])),
            topic_patterns=comp(rel.get("topic_patterns", [])),
            exclude_title_patterns=comp(rel.get("exclude_title_patterns", [])),
            min_topic_matches=int(rel.get("min_topic_matches", 3)),
            max_age_days=max_age_days,
            min_salary=min_salary,
            today=today or date.today(),
            local_places=re.compile("|".join(local_places), re.I) if local_places else None,
            foreign_places=re.compile("|".join(foreign_places), re.I) if foreign_places else None,
            region=region,
        )

    @classmethod
    def from_settings(cls, search_terms: dict, settings, today: date | None = None) -> Rules:
        c = settings.candidate
        return cls.from_config(search_terms, max_age_days=settings.thresholds.max_job_age_days,
                               min_salary=settings.preferences.min_gross_annual_salary, today=today,
                               local_places=c.local_place_patterns, foreign_places=c.foreign_place_patterns,
                               region=c.country)


def _salary_upper_annual(salary: dict | None) -> float | None:
    """Highest advertised annual pay, only when computable without assumptions."""
    if not salary:
        return None
    s = Salary.model_validate(salary)
    top = s.amount_max if s.amount_max is not None else None
    if top is None:
        return None          # only a minimum (e.g. 'ab ...'): pay may be higher -> never reject
    if s.period == "annual":
        return top
    if s.period == "monthly" and s.payments_per_year:
        return top * s.payments_per_year
    return None


def decide(job: JobRow, rules: Rules) -> tuple[JobStatus, str]:
    """Stage 1 decision for one job: (status, reason)."""
    if job.deadline and job.deadline < rules.today:
        return JobStatus.FILTERED_OUT, f"application deadline passed ({job.deadline})"
    # Age = how long since WE found it on a live listing. A listing still showing an old
    # posting means the job is still open, so the publication date alone never filters.
    if job.discovered_at and job.discovered_at.date() < rules.today - timedelta(days=rules.max_age_days):
        return JobStatus.FILTERED_OUT, f"found more than {rules.max_age_days} days ago ({job.discovered_at:%Y-%m-%d})"
    loc = job.location or ""
    if (loc and rules.foreign_places and rules.foreign_places.search(loc)
            and not (rules.local_places and rules.local_places.search(loc))):
        return JobStatus.FILTERED_OUT, f"outside {rules.region} ({loc})"
    upper = _salary_upper_annual(job.salary)
    if upper is not None and upper < rules.min_salary:
        return JobStatus.FILTERED_OUT, f"advertised maximum salary {upper:,.0f} per year is below your minimum {rules.min_salary:,.0f}"
    excluded = next((p.pattern for p in rules.exclude_title_patterns if p.search(job.title)), None)
    if excluded:
        return JobStatus.FILTERED_OUT, f"title needs a degree/status you do not have (rule: {excluded})"
    title_hits = [p.pattern for p in rules.title_patterns if p.search(job.title)]
    if len(job.description or "") < MIN_TEXT_CHARS:
        if title_hits:
            return JobStatus.NEEDS_TEXT, "only a title is known - import the full ad to evaluate it"
        return JobStatus.FILTERED_OUT, "no job text and title does not match your fields"
    if title_hits:
        return JobStatus.RELEVANT, "title matches your fields"
    text = f"{job.title}\n{job.description}"
    topics = {p.pattern for p in rules.topic_patterns if p.search(text)}
    if len(topics) >= rules.min_topic_matches:
        return JobStatus.RELEVANT, f"text mentions {len(topics)} of your topics"
    return JobStatus.FILTERED_OUT, f"not related to your fields (title no match, {len(topics)} topic words in text)"


# ---------------- deduplication ----------------

_GENDER = re.compile(r"\((?:[mwfdx]\s*/\s*)+[mwfdx]\)|\b[mwfdx]/[mwfdx](?:/[mwfdx])?\b|\*in\b|:in\b|/in\b|innen\b", re.I)
_LEGAL = re.compile(r"\b(gmbh|ag|kg|og|e\.?u\.?|ltd|inc|mbh|se|co|plc|corp|group|austria|österreich)\b\.?", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_title(title: str) -> str:
    return _norm(_GENDER.sub(" ", title))


def norm_employer(employer: str) -> str:
    return _norm(_LEGAL.sub(" ", employer or ""))


def same_employer(a: str, b: str) -> bool:
    a, b = norm_employer(a), norm_employer(b)
    if not a or not b or "unknown" in (a, b):
        return False
    return a == b or a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.85


_NUM = re.compile(r"\d+")


def _text_similar(a: str, b: str) -> bool:
    a, b = _norm(a or "")[:2000], _norm(b or "")[:2000]
    if len(a) < MIN_TEXT_CHARS or len(b) < MIN_TEXT_CHARS:
        return True          # one side has no text (e.g. alert stub): title + employer decide
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.5


def employer_key(employer: str) -> str:
    """Grouping key: the first 4 letters of the normalised employer name."""
    return norm_employer(employer).replace(" ", "")[:4]


def same_job(a: JobRow, b: JobRow) -> bool:
    """Same vacancy published on two different sites. Within one site, the site's own
    listing already separates jobs, so equal titles there are different openings."""
    if a.source == b.source or not same_employer(a.employer, b.employer):
        return False
    ta, tb = norm_title(a.title), norm_title(b.title)
    if _NUM.findall(ta) != _NUM.findall(tb):          # "30 h/week" vs "24 h/week", Ref numbers
        return False
    if not (ta == tb or SequenceMatcher(None, ta, tb).ratio() >= 0.9):
        return False
    return _text_similar(a.description, b.description)


@dataclass
class Stage1Result:
    counts: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0


def run_stage1(session: Session, rules: Rules) -> Stage1Result:
    """Decide every NEW job, then mark cross-source duplicates among live jobs."""
    res = Stage1Result()
    for job in session.scalars(select(JobRow).where(JobRow.status == JobStatus.NEW.value)):
        status, reason = decide(job, rules)
        job.status, job.filter_reason = status.value, reason
        res.counts[status.value] = res.counts.get(status.value, 0) + 1
    # Re-check earlier duplicate decisions so the matching rules can be improved safely.
    for job in session.scalars(select(JobRow).where(JobRow.status == JobStatus.DUPLICATE.value)):
        status, reason = decide(job, rules)
        job.status, job.filter_reason, job.duplicate_of = status.value, reason, None
    session.flush()
    live = list(session.scalars(select(JobRow).where(JobRow.status.in_(
        [JobStatus.RELEVANT.value, JobStatus.EVALUATED.value, JobStatus.NEEDS_TEXT.value]))))
    # keep the copy with most text (evaluated ones win, so no evaluation is wasted)
    live.sort(key=lambda j: (j.status != JobStatus.EVALUATED.value, -len(j.description or ""), j.id))
    # Only jobs whose employer names start alike can be the same vacancy: compare within
    # these groups instead of every pair (keeps the daily run fast with thousands of jobs).
    kept: dict[str, list[JobRow]] = {}
    for job in live:
        group = kept.setdefault(employer_key(job.employer), [])
        original = next((k for k in group if same_job(k, job)), None)
        if original is None:
            group.append(job)
            continue
        job.status = JobStatus.DUPLICATE.value
        job.duplicate_of = original.id
        job.filter_reason = f"same job as #{original.id} ({original.source})"
        res.duplicates += 1
    return res
