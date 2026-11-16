"""Store collected jobs. Exact 'seen before' checks only; real deduplication
across sources is Phase 3."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.tables import JobRow
from ..models.job import Job
from .base import CollectedJob


def job_to_row(job: Job, external_id: str | None, run_id: int | None) -> JobRow:
    salary = job.salary
    return JobRow(
        title=job.title, employer=job.employer, location=job.location, url=job.url,
        source=job.source, published_at=job.published_at, discovered_at=job.discovered_at,
        retrieved_at=job.retrieved_at, deadline=job.deadline,
        salary=salary.model_dump(mode="json") if salary else None,
        salary_annual_min=salary.annual_gross_min() if salary else None,
        employment_type=job.employment_type, description=job.description,
        language=job.language.value if job.language else None, status=job.status.value,
        content_hash=job.content_hash, external_id=external_id, run_id=run_id,
    )


def already_seen(session: Session, cj: CollectedJob) -> bool:
    j = cj.job
    conds = [(JobRow.source == j.source) & (JobRow.content_hash == j.content_hash)]
    if cj.external_id:
        conds.append((JobRow.source == j.source) & (JobRow.external_id == cj.external_id))
    if j.url:
        conds.append(JobRow.url == j.url)
    return session.scalar(select(JobRow.id).where(or_(*conds)).limit(1)) is not None


def store_jobs(session: Session, jobs: list[CollectedJob], run_id: int | None) -> tuple[int, int]:
    """Returns (new, skipped)."""
    new = skipped = 0
    for cj in jobs:
        if already_seen(session, cj):
            skipped += 1
            continue
        session.add(job_to_row(cj.job, cj.external_id, run_id))
        session.flush()
        new += 1
    return new, skipped
