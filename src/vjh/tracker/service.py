"""Application tracker. Every status change is a new row in application_events (append-only,
enforced by database triggers). Automatic changes only move forward and never past 'approved':
'applied' and everything after it are always set by the user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.tables import ApplicationEventRow, ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from ..models.common import ApplicationStatus as S
from ..models.common import ApprovalStatus

PIPELINE = [S.NEW, S.RECOMMENDED, S.UNDER_REVIEW, S.DOCUMENTS_GENERATED, S.APPROVED, S.APPLIED,
            S.INTERVIEW, S.OFFER]
TERMINAL = {S.REJECTED, S.ARCHIVED, S.OFFER}
AUTO_MAX = S.APPROVED            # automation never sets applied / interview / offer / rejected
DEFAULT_FOLLOW_UP_DAYS = 10


class TrackerError(ValueError):
    pass


def _rank(status: str) -> int:
    s = S(status)
    return PIPELINE.index(s) if s in PIPELINE else 99


def ensure_application(session: Session, job_id: int, now: datetime | None = None) -> ApplicationRow:
    app = session.scalar(select(ApplicationRow).where(ApplicationRow.job_id == job_id))
    if app is not None:
        return app
    if session.get(JobRow, job_id) is None:
        raise TrackerError(f"no job #{job_id}")
    app = ApplicationRow(job_id=job_id, status=S.NEW.value, documents_submitted=[], notes="")
    session.add(app)
    session.flush()
    session.add(ApplicationEventRow(application_id=app.id, from_status=None, to_status=S.NEW.value,
                                    occurred_at=now or datetime.now(), note="application record created"))
    return app


def latest_approved_documents(session: Session, job_id: int) -> dict[str, GeneratedDocumentRow]:
    """Newest approved document per (type, language)."""
    out: dict[str, GeneratedDocumentRow] = {}
    rows = session.scalars(select(GeneratedDocumentRow).where(
        GeneratedDocumentRow.job_id == job_id,
        GeneratedDocumentRow.approval_status == ApprovalStatus.APPROVED.value)
        .order_by(GeneratedDocumentRow.version))
    for d in rows:
        out[f"{d.doc_type}_{d.language}"] = d
    return out


@dataclass
class Change:
    app: ApplicationRow
    from_status: str | None
    to_status: str
    warnings: list[str]


def set_status(session: Session, job_id: int, to: str, *, note: str = "", when: date | None = None,
               interview_at: datetime | None = None, follow_up: date | None = None,
               response: str | None = None, now: datetime | None = None,
               follow_up_days: int = DEFAULT_FOLLOW_UP_DAYS) -> Change:
    """Manual status change by the user. Any status may be set (corrections are allowed);
    the history keeps every step."""
    try:
        target = S(to)
    except ValueError as exc:
        raise TrackerError(f"unknown status '{to}'. Use one of: {', '.join(s.value for s in S)}") from exc
    now = now or datetime.now()
    day = when or now.date()
    app = ensure_application(session, job_id, now)
    old = app.status
    warnings: list[str] = []

    if target == S.DOCUMENTS_GENERATED and app.date_prepared is None:
        app.date_prepared = day
    if target == S.APPROVED:
        app.date_approved = day
    if target == S.APPLIED:
        app.date_submitted = day
        app.follow_up_date = follow_up or day + timedelta(days=follow_up_days)
        docs = latest_approved_documents(session, job_id)
        app.documents_submitted = sorted(d.id for d in docs.values())
        if not docs:
            warnings.append("no approved documents recorded for this job (vjh docs approve <id>)")
    if target == S.INTERVIEW:
        if interview_at is None and app.interview_date is None:
            warnings.append("no interview date given (add one with --interview \"YYYY-MM-DD HH:MM\")")
        if interview_at:
            app.interview_date = interview_at
    if target in (S.REJECTED, S.OFFER):
        app.result = target.value
    if follow_up and target != S.APPLIED:
        app.follow_up_date = follow_up
    if response:
        app.employer_response = response
    if note:
        stamp = f"[{day.isoformat()}] {note}"
        app.notes = f"{app.notes}\n{stamp}".strip() if app.notes else stamp

    app.status = target.value
    session.add(ApplicationEventRow(application_id=app.id, from_status=old, to_status=target.value,
                                    occurred_at=now, note=note or ("manual" if old != target.value else "update")))
    return Change(app, old, target.value, warnings)


def auto_advance(session: Session, job_id: int, to: S, note: str, now: datetime | None = None) -> bool:
    """Automatic, forward-only move. Never beyond 'approved', never out of a final state."""
    if _rank(to.value) > _rank(AUTO_MAX.value):
        raise TrackerError("automation may not set this status")
    app = ensure_application(session, job_id, now)
    current = S(app.status)
    if current in TERMINAL or _rank(current.value) >= _rank(to.value):
        return False
    old = app.status
    app.status = to.value
    day = (now or datetime.now()).date()
    if to == S.DOCUMENTS_GENERATED and app.date_prepared is None:
        app.date_prepared = day
    if to == S.APPROVED and app.date_approved is None:
        app.date_approved = day
    session.add(ApplicationEventRow(application_id=app.id, from_status=old, to_status=to.value,
                                    occurred_at=now or datetime.now(), note=f"automatic: {note}"))
    return True


def sync_recommendations(session: Session, min_score: float, now: datetime | None = None) -> int:
    """Evaluated jobs with score >= min_score or an 'apply' recommendation -> 'recommended'.
    Potentially ineligible jobs are included (flagged in the lists), never hidden."""
    moved = 0
    for job in session.scalars(select(JobRow).where(JobRow.status == "evaluated")):
        ev = session.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job.id)
                            .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))
        if ev is None:
            continue
        if ev.overall_score >= min_score or ev.recommendation == "apply":
            why = f"score {ev.overall_score:.0f}, AI recommendation '{ev.recommendation}'"
            moved += auto_advance(session, job.id, S.RECOMMENDED, why, now)
    return moved


def sync_documents(session: Session, job_id: int, now: datetime | None = None) -> str | None:
    """After documents change: generated -> 'documents_generated'; an approved CV AND an
    approved cover letter (same language) -> 'approved'. Returns the new status, if any."""
    docs = list(session.scalars(select(GeneratedDocumentRow).where(GeneratedDocumentRow.job_id == job_id)))
    if not docs:
        return None
    new = None
    if auto_advance(session, job_id, S.DOCUMENTS_GENERATED, "documents created", now):
        new = S.DOCUMENTS_GENERATED.value
    approved = latest_approved_documents(session, job_id)
    langs = {k.split("_")[-1] for k in approved}
    if any(f"cv_{lang}" in approved and f"cover_letter_{lang}" in approved for lang in langs):
        if auto_advance(session, job_id, S.APPROVED, "CV and cover letter approved", now):
            new = S.APPROVED.value
    return new


def history(session: Session, job_id: int) -> list[ApplicationEventRow]:
    app = session.scalar(select(ApplicationRow).where(ApplicationRow.job_id == job_id))
    if app is None:
        return []
    return list(session.scalars(select(ApplicationEventRow).where(ApplicationEventRow.application_id == app.id)
                                .order_by(ApplicationEventRow.occurred_at, ApplicationEventRow.id)))


@dataclass
class DueItem:
    when: date
    kind: str            # follow_up | interview | deadline
    job_id: int
    title: str
    employer: str
    status: str


def due_items(session: Session, today: date, days_ahead: int = 7) -> list[DueItem]:
    """Follow-ups and interviews due soon (or overdue), and deadlines of jobs not yet applied to."""
    horizon = today + timedelta(days=days_ahead)
    items: list[DueItem] = []
    for app in session.scalars(select(ApplicationRow)):
        job = session.get(JobRow, app.job_id)
        st = S(app.status)
        if app.follow_up_date and st in (S.APPLIED,) and app.follow_up_date <= horizon:
            items.append(DueItem(app.follow_up_date, "follow_up", job.id, job.title, job.employer, app.status))
        if app.interview_date and st == S.INTERVIEW and app.interview_date.date() >= today \
                and app.interview_date.date() <= horizon:
            items.append(DueItem(app.interview_date.date(), "interview", job.id, job.title, job.employer, app.status))
        if job.deadline and _rank(app.status) < _rank(S.APPLIED.value) and st not in TERMINAL \
                and today <= job.deadline <= horizon:
            items.append(DueItem(job.deadline, "deadline", job.id, job.title, job.employer, app.status))
    return sorted(items, key=lambda i: (i.when, i.kind))
