"""Read-only data access for the dashboard. Plain functions returning DataFrames/dicts,
so they can be tested without Streamlit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.tables import (
    ApiUsageRow,
    ApplicationEventRow,
    ApplicationRow,
    FeedbackRow,
    GeneratedDocumentRow,
    JobEvaluationRow,
    JobRow,
    RunRow,
    SourceRow,
)
from ..models.job import Salary
from ..tracker.service import due_items, history

_DE = re.compile(r"\b(und|der|die|das|mit|für|wir|sie|ihre|ihr|bei|eine|einer|sowie|oder|von)\b", re.I)
_EN = re.compile(r"\b(and|the|with|for|we|you|your|our|of|an|to|in|as|or|will)\b", re.I)


def guess_language(text: str) -> str:
    """'de', 'en' or 'unknown' by counting frequent function words."""
    sample = (text or "")[:4000]
    de, en = len(_DE.findall(sample)), len(_EN.findall(sample))
    if de + en < 5:
        return "unknown"
    return "de" if de > en else "en"


# Job titles as links to the ad. The title travels in the link's #fragment so the table can show it
# (Streamlit LinkColumn shows the first regex group). Links that already use a #fragment are left alone.
TITLE_LINK_DISPLAY = r"^[^#]*#(.+)$"


def is_web_url(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def ad_url(url: str | None, title: str, employer: str) -> str:
    """The ad's own address, or a web search for title + employer when none is stored (or it is not http/https)."""
    if is_web_url(url):
        return url.strip()
    return "https://www.google.com/search?q=" + quote_plus(f"{title} {employer}".strip())


def title_link(url: str | None, title: str, employer: str) -> str:
    u = ad_url(url, title, employer)
    label = title if is_web_url(url) else f"🔎 {title}"
    return u if "#" in u else f"{u}#{label}"


def md_link(text: str, url: str | None, employer: str, search_title: str | None = None) -> str:
    u = ad_url(url, search_title or text, employer)
    safe = text.replace("[", "(").replace("]", ")")
    return f"[{safe}](<{u}>)"


def salary_text(raw: dict | None) -> str:
    if not raw:
        return ""
    s = Salary.model_validate(raw)
    if s.amount_min is None:
        return s.source_text[:80]
    rng = f"{s.amount_min:,.0f}" + (f"-{s.amount_max:,.0f}" if s.amount_max else "+")
    pay = f", {s.payments_per_year}x" if s.payments_per_year else ""
    return f"{s.currency} {rng} {s.period or ''}{pay}".strip()


def _latest_evals(session: Session) -> dict[int, JobEvaluationRow]:
    out: dict[int, JobEvaluationRow] = {}
    for ev in session.scalars(select(JobEvaluationRow).order_by(JobEvaluationRow.evaluated_at, JobEvaluationRow.id)):
        out[ev.job_id] = ev
    return out


JOB_COLUMNS = ["id", "title", "employer", "location", "source", "job_status", "application", "applied",
               "applied_on", "score", "category", "recommendation", "eligibility", "ineligible", "language",
               "salary", "salary_annual_min", "published", "discovered", "deadline", "filter_reason", "url"]


def applied_jobs(session: Session) -> dict[int, date | None]:
    """Jobs the user has sent an application for, with the date it was sent (None if not recorded).
    A job stays 'applied' after a later interview, offer, rejection or archiving (from the history)."""
    sent = {a.job_id: a.date_submitted for a in session.scalars(select(ApplicationRow))}
    return {job: sent.get(job) for job, reached in _reached(session).items() if "applied" in reached}


def applied_label(applied: bool, when: date | None) -> str:
    """'✅ Applied 18.11.2026' / '✅ Applied' / ''. One wording everywhere (tables, report, details)."""
    if not applied:
        return ""
    return "✅ Applied" + (f" {when:%d.%m.%Y}" if when is not None and not pd.isna(when) else "")


# Sort orders for job tables: label -> (column, ascending). Jobs without that date go last.
JOB_SORTS = {
    "Best score first": ("score", False),
    "Newest posting first": ("published", False),
    "Oldest posting first": ("published", True),
    "Newest found first": ("discovered", False),
    "Deadline soonest first": ("deadline", True),
}


def sort_jobs(df: pd.DataFrame, how: str) -> pd.DataFrame:
    col, asc = JOB_SORTS[how]
    if col == "score":
        return df.sort_values(["score", "discovered"], ascending=[False, False], na_position="last")
    key = pd.to_datetime(df[col], errors="coerce")
    order = key.sort_values(ascending=asc, na_position="last", kind="stable").index
    return df.loc[order]


def jobs_frame(session: Session) -> pd.DataFrame:
    evals = _latest_evals(session)
    apps = {a.job_id: a.status for a in session.scalars(select(ApplicationRow))}
    applied = applied_jobs(session)
    rows = []
    for j in session.scalars(select(JobRow)):
        ev = evals.get(j.id)
        rows.append({
            "id": j.id, "title": j.title, "employer": j.employer, "location": j.location or "",
            "source": j.source, "job_status": j.status, "application": apps.get(j.id, ""),
            "applied": j.id in applied, "applied_on": applied.get(j.id),
            "score": ev.overall_score if ev else None, "category": ev.category if ev else "",
            "recommendation": ev.recommendation if ev else "", "eligibility": ev.eligibility_result if ev else "",
            "ineligible": bool(ev.potentially_ineligible) if ev else False,
            "language": j.language or guess_language(j.description), "salary": salary_text(j.salary),
            "salary_annual_min": j.salary_annual_min, "published": j.published_at,
            "discovered": j.discovered_at.date() if j.discovered_at else None, "deadline": j.deadline,
            "filter_reason": j.filter_reason or "", "url": j.url or "",
        })
    df = pd.DataFrame(rows, columns=JOB_COLUMNS)
    return df.sort_values(["score", "discovered"], ascending=[False, False], na_position="last").reset_index(drop=True)


@dataclass
class JobFilters:
    min_score: float = 0
    max_score: float = 100
    include_unscored: bool = True
    text: str = ""                     # title or employer contains
    employer: str = ""
    location: str = ""
    categories: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    job_statuses: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()
    min_salary: float | None = None    # only jobs with a known annual minimum >= this
    published_since: date | None = None
    hide_applied: bool = False         # leave out jobs already applied to


def filter_jobs(df: pd.DataFrame, f: JobFilters) -> pd.DataFrame:
    out = df.copy()
    scored = out["score"].notna()
    in_range = scored & (out["score"] >= f.min_score) & (out["score"] <= f.max_score)
    out = out[in_range | (~scored & f.include_unscored)]
    if f.text:
        t = f.text.lower()
        out = out[out["title"].str.lower().str.contains(t, regex=False)
                  | out["employer"].str.lower().str.contains(t, regex=False)]
    if f.employer:
        out = out[out["employer"].str.lower().str.contains(f.employer.lower(), regex=False)]
    if f.location:
        out = out[out["location"].str.lower().str.contains(f.location.lower(), regex=False)]
    if f.categories:
        out = out[out["category"].isin(f.categories)]
    if f.languages:
        out = out[out["language"].isin(f.languages)]
    if f.job_statuses:
        out = out[out["job_status"].isin(f.job_statuses)]
    if f.applications:
        out = out[out["application"].isin(f.applications)]
    if f.min_salary is not None:
        out = out[out["salary_annual_min"].notna() & (out["salary_annual_min"] >= f.min_salary)]
    if f.published_since is not None:
        out = out[out["published"].notna() & (out["published"] >= f.published_since)]
    if f.hide_applied:
        out = out[~out["applied"].astype(bool)]
    return out


def job_detail(session: Session, job_id: int) -> dict | None:
    j = session.get(JobRow, job_id)
    if j is None:
        return None
    ev = session.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job_id)
                        .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))
    app = session.scalar(select(ApplicationRow).where(ApplicationRow.job_id == job_id))
    docs = list(session.scalars(select(GeneratedDocumentRow).where(GeneratedDocumentRow.job_id == job_id)
                                .order_by(GeneratedDocumentRow.id)))
    fb = list(session.scalars(select(FeedbackRow).where(FeedbackRow.job_id == job_id)))
    applied = applied_jobs(session)
    return {
        "applied": job_id in applied, "applied_on": applied.get(job_id),
        "job": j, "evaluation": ev, "ai": ev.ai_output if ev else None, "application": app,
        "history": history(session, job_id), "documents": docs, "feedback": fb,
        "salary": salary_text(j.salary), "language": j.language or guess_language(j.description),
    }


def documents_frame(session: Session) -> pd.DataFrame:
    rows = []
    for d in session.scalars(select(GeneratedDocumentRow).order_by(GeneratedDocumentRow.id.desc())):
        j = session.get(JobRow, d.job_id)
        rows.append({"doc": d.id, "job": d.job_id, "employer": j.employer if j else "", "type": d.doc_type,
                     "language": d.language, "version": d.version, "status": d.approval_status,
                     "created": d.created_at, "file": d.file_path})
    return pd.DataFrame(rows, columns=["doc", "job", "employer", "type", "language", "version", "status",
                                       "created", "file"])


def applications_frame(session: Session) -> pd.DataFrame:
    evals = _latest_evals(session)
    rows = []
    for a in session.scalars(select(ApplicationRow)):
        j = session.get(JobRow, a.job_id)
        ev = evals.get(a.job_id)
        rows.append({"job": a.job_id, "status": a.status, "title": j.title, "employer": j.employer,
                     "score": ev.overall_score if ev else None, "published": j.published_at,
                     "deadline": j.deadline, "applied": a.date_submitted, "follow_up": a.follow_up_date,
                     "interview": a.interview_date, "response": a.employer_response or "", "url": j.url or ""})
    return pd.DataFrame(rows, columns=["job", "status", "title", "employer", "score", "published", "deadline",
                                       "applied", "follow_up", "interview", "response", "url"])


STALE_DAYS = 2


def sources_frame(session: Session, now: datetime) -> pd.DataFrame:
    rows = []
    for r in session.scalars(select(SourceRow).order_by(SourceRow.name)):
        if not r.active:
            state = "inactive"
        elif r.error_status:
            state = "failing"
        elif r.automatic_supported and r.last_success is None:
            state = "never collected"
        elif r.automatic_supported and now - r.last_success > timedelta(days=STALE_DAYS):
            state = "stale"
        elif r.automatic_supported:
            state = "ok"
        else:
            state = "alerts / manual"
        rows.append({"source": r.name, "state": state, "method": r.collector or r.collection_method,
                     "automatic": r.automatic_supported, "last_success": r.last_success,
                     "jobs_last_time": r.last_jobs_found, "error": r.error_status or "",
                     "restrictions": r.access_restrictions, "url": r.url})
    return pd.DataFrame(rows, columns=["source", "state", "method", "automatic", "last_success",
                                       "jobs_last_time", "error", "restrictions", "url"])


def runs_frame(session: Session, limit: int = 30) -> pd.DataFrame:
    rows = []
    for r in session.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(limit)):
        kind = "evaluate" if r.jobs_evaluated or r.api_calls else "collect"
        rows.append({"run": r.id, "kind": kind, "started": r.started_at, "status": r.status,
                     "new_jobs": r.jobs_collected, "evaluated": r.jobs_evaluated, "ai_calls": r.api_calls,
                     "cost_usd": r.estimated_cost_usd, "errors": len(r.errors or []),
                     "error_detail": "; ".join(e.get("error") or f"{e.get('source')}: {e.get('error')}"
                                               for e in (r.errors or []))[:500],
                     "log": r.log_path or ""})
    return pd.DataFrame(rows, columns=["run", "kind", "started", "status", "new_jobs", "evaluated", "ai_calls",
                                       "cost_usd", "errors", "error_detail", "log"])


def latest_log(data_dir: Path, max_chars: int = 20000) -> tuple[Path | None, str]:
    logs = sorted((data_dir / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return None, ""
    text = logs[0].read_text(encoding="utf-8", errors="replace")
    return logs[0], text[-max_chars:]


def overview(session: Session, now: datetime, settings) -> dict:
    today = now.date()
    start = datetime.combine(today, datetime.min.time())
    status_counts = dict(session.execute(select(JobRow.status, func.count()).group_by(JobRow.status)).all())
    app_counts = dict(session.execute(select(ApplicationRow.status, func.count())
                                      .group_by(ApplicationRow.status)).all())
    new_today = session.scalar(select(func.count()).select_from(JobRow).where(JobRow.discovered_at >= start)) or 0
    usage = session.execute(select(ApiUsageRow.purpose, func.count(), func.coalesce(func.sum(ApiUsageRow.estimated_cost_usd), 0))
                            .where(ApiUsageRow.called_at >= start).group_by(ApiUsageRow.purpose)).all()
    src = sources_frame(session, now)
    problems = src[src["state"].isin(["failing", "stale", "never collected"])]
    last_run = session.scalar(select(RunRow).order_by(RunRow.id.desc()).limit(1))
    return {
        "new_today": new_today, "job_status": status_counts, "applications": app_counts,
        "usage_today": [{"purpose": p, "calls": n, "cost_usd": round(c, 4)} for p, n, c in usage],
        "evaluations_today": sum(n for p, n, _ in usage if p.startswith("evaluate")),
        "subscription_limit": settings.llm.subscription_daily_max_jobs,
        "cost_cap": settings.llm.daily_cost_cap_usd, "backend": settings.llm.backend,
        "due": due_items(session, today, 7), "source_problems": problems,
        "last_run": last_run,
    }


# ---------------- progress (Progress page) ----------------

PROGRESS_STEPS = ["Found", "Passed the filter", "Evaluated by AI", "Recommended", "Applied", "Interview", "Offer"]
_RESPONDED = {"interview", "offer", "rejected"}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())        # Monday


def _reached(session: Session) -> dict[int, set[str]]:
    """Every status each job's application has ever had (from the append-only history)."""
    out: dict[int, set[str]] = {}
    apps = {a.id: a for a in session.scalars(select(ApplicationRow))}
    for a in apps.values():
        out[a.job_id] = {a.status}
        if a.date_submitted:
            out[a.job_id].add("applied")
    for e in session.scalars(select(ApplicationEventRow)):
        a = apps.get(e.application_id)
        if a is not None:
            out[a.job_id].add(e.to_status)
    for s in out.values():                          # interview/offer imply it was sent
        if s & {"interview", "offer"}:
            s.add("applied")
    return out


def progress(session: Session, today: date, recommend_min_score: float, weekly_goal: int,
             weeks: int = 12) -> dict:
    """Funnel, weekly activity, response rates and the weekly goal. Read-only."""
    jobs = list(session.scalars(select(JobRow).where(JobRow.status != "duplicate")))
    evals = _latest_evals(session)
    reached = _reached(session)
    apps = list(session.scalars(select(ApplicationRow)))

    def ever(status: str) -> set[int]:
        return {j for j, s in reached.items() if status in s}

    applied_ids = ever("applied")
    funnel = {
        "Found": len(jobs),
        "Passed the filter": sum(j.status not in ("new", "filtered_out") for j in jobs),
        "Evaluated by AI": len(evals),
        "Recommended": sum(ev.overall_score is not None and ev.overall_score >= recommend_min_score
                           for ev in evals.values()),
        "Applied": len(applied_ids),
        "Interview": len(ever("interview")),
        "Offer": len(ever("offer")),
    }
    responded = {a.job_id for a in apps if a.job_id in applied_ids
                 and ((reached.get(a.job_id, set()) & _RESPONDED) or a.employer_response)}
    n = len(applied_ids)
    rates = {"applied": n, "responses": len(responded), "interviews": funnel["Interview"],
             "offers": funnel["Offer"], "rejections": len(ever("rejected") & applied_ids),
             "waiting": n - len(responded),
             "response_rate": round(100 * len(responded) / n) if n else None,
             "interview_rate": round(100 * funnel["Interview"] / n) if n else None}

    # weekly activity
    first = _week_start(today) - timedelta(weeks=weeks - 1)
    weekly = {first + timedelta(weeks=i): {"found": 0, "applied": 0, "interviews": 0} for i in range(weeks)}
    for j in jobs:
        if j.discovered_at and (w := _week_start(j.discovered_at.date())) in weekly:
            weekly[w]["found"] += 1
    for a in apps:
        if a.date_submitted and (w := _week_start(a.date_submitted)) in weekly:
            weekly[w]["applied"] += 1
    app_job = {a.id: a.job_id for a in apps}
    seen_interview: set[int] = set()
    for e in session.scalars(select(ApplicationEventRow).where(ApplicationEventRow.to_status == "interview")
                             .order_by(ApplicationEventRow.occurred_at)):
        job = app_job.get(e.application_id)
        if job in seen_interview:
            continue
        seen_interview.add(job)
        if (w := _week_start(e.occurred_at.date())) in weekly:
            weekly[w]["interviews"] += 1
    weekly_df = pd.DataFrame([{"week": w, **v} for w, v in weekly.items()],
                             columns=["week", "found", "applied", "interviews"])

    # weekly goal and streak (weeks in a row that reached the goal; this week counts once reached)
    this_week = weekly[_week_start(today)]["applied"]
    streak = 0
    if weekly_goal > 0:
        ordered = sorted(weekly)
        if this_week >= weekly_goal:
            streak = 1
        for w in reversed(ordered[:-1]):
            if weekly[w]["applied"] < weekly_goal:
                break
            streak += 1
    fb = list(session.scalars(select(FeedbackRow)))
    return {"funnel": funnel, "rates": rates, "weekly": weekly_df, "this_week_applied": this_week,
            "weekly_goal": weekly_goal, "streak": streak,
            "feedback_good": sum(f.accepted for f in fb), "feedback_bad": sum(not f.accepted for f in fb)}


# ---------------- next steps (Overview) ----------------

@dataclass
class NextStep:
    icon: str          # Material icon, e.g. ":material/event:"
    text: str
    page: str          # dashboard page that handles it
    urgent: bool = False


def next_steps(session: Session, today: date, recommend_min_score: float, weekly_goal: int,
               now: datetime | None = None) -> list[NextStep]:
    """The few things worth doing now, most urgent first (empty = nothing to do)."""
    steps: list[NextStep] = []
    due = due_items(session, today, 7)
    overdue = [d for d in due if d.when < today]
    if overdue:
        steps.append(NextStep(":material/alarm:", f"{len(overdue)} follow-up/interview/deadline date(s) are overdue",
                              "Applications", urgent=True))
    soon = len(due) - len(overdue)
    if soon:
        steps.append(NextStep(":material/event:", f"{soon} date(s) due in the next 7 days", "Applications"))
    apps = dict(session.execute(select(ApplicationRow.status, func.count()).group_by(ApplicationRow.status)).all())
    if apps.get("approved"):
        steps.append(NextStep(":material/send:", f"{apps['approved']} application(s) approved and ready to send "
                              "- send them yourself, then mark them applied", "Applications"))
    docs = dict(session.execute(select(GeneratedDocumentRow.approval_status, func.count())
                                .group_by(GeneratedDocumentRow.approval_status)).all())
    if docs.get("qa_passed"):
        steps.append(NextStep(":material/fact_check:", f"{docs['qa_passed']} document(s) passed QA and wait for "
                              "your approval", "Documents"))
    if docs.get("qa_failed"):
        steps.append(NextStep(":material/report:", f"{docs['qa_failed']} document(s) failed QA - read the report "
                              "or create a new version", "Documents"))
    if apps.get("recommended"):
        steps.append(NextStep(":material/star:", f"{apps['recommended']} recommended job(s) to look at and rate",
                              "Jobs"))
    if weekly_goal:
        done = progress(session, today, recommend_min_score, weekly_goal, weeks=1)["this_week_applied"]
        if done < weekly_goal:
            steps.append(NextStep(":material/flag:", f"{weekly_goal - done} more application(s) to reach this "
                                  "week's goal", "Progress"))
    problems = sources_frame(session, now or datetime.combine(today, datetime.min.time()))
    bad = problems[problems["state"].isin(["failing", "stale"])]
    if len(bad):
        steps.append(NextStep(":material/sync_problem:", f"{len(bad)} automatic source(s) failing or stale",
                              "Sources"))
    return sorted(steps, key=lambda s: not s.urgent)
