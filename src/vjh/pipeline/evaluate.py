"""Stage 2: AI evaluation of relevant, unseen jobs.

Order per job: cost cap -> cache -> one API call -> validate -> Python score -> store.
Cache key: (job content hash, master profile version, model) - ARCHITECTURE.md §3.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..db.tables import ApiUsageRow, JobEvaluationRow, JobRow, RunRow
from ..llm.claude_code import BackendUnavailable
from ..llm.client import LLM, estimate_cost
from ..llm.prompts import build_system_prompt, build_user_message
from ..llm.schema import evaluation_schema
from ..models.common import JobStatus
from ..models.evaluation import AIEvaluation
from ..models.profile import MasterProfile
from ..models.settings import Settings
from ..scoring.model import clean_evidence, compute

MAX_AD_CHARS = 60_000   # longer texts are skipped (and reported), never silently cut


@dataclass
class EvalSummary:
    run_id: int | None = None
    evaluated: int = 0
    from_cache: int = 0
    failed: int = 0
    skipped: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped_by_cap: bool = False
    stopped_reason: str | None = None     # backend unavailable (not logged in, usage limit)
    pending: int = 0
    outdated: int = 0                     # evaluated with an older profile/model, not refreshed
    notes: list[str] = field(default_factory=list)


def spent_today(session: Session, now: datetime) -> float:
    start = datetime.combine(now.date(), time.min)
    return float(session.scalar(select(func.coalesce(func.sum(ApiUsageRow.estimated_cost_usd), 0.0))
                                .where(ApiUsageRow.called_at >= start)) or 0.0)


PURPOSES = {"api": "evaluate", "claude_code": "evaluate_subscription"}


def calls_today(session: Session, now: datetime, purpose: str) -> int:
    start = datetime.combine(now.date(), time.min)
    return session.scalar(select(func.count()).select_from(ApiUsageRow).where(
        ApiUsageRow.called_at >= start, ApiUsageRow.purpose == purpose)) or 0


def failures_for(session: Session, job: JobRow, model: str) -> int:
    return session.scalar(select(func.count()).select_from(ApiUsageRow).where(
        ApiUsageRow.job_id == job.id, ApiUsageRow.success.is_(False),
        ApiUsageRow.purpose.in_(PURPOSES.values()))) or 0


def outdated_evaluations(session: Session, profile_version: int, model: str) -> list[int]:
    """Evaluated jobs whose newest evaluation used another profile version or model
    (cache key changed -> ARCHITECTURE.md §3: re-evaluate)."""
    out = []
    for job in session.scalars(select(JobRow).where(JobRow.status == JobStatus.EVALUATED.value)
                               .order_by(JobRow.discovered_at.desc(), JobRow.id)):
        latest = session.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job.id)
                                .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc())
                                .limit(1))
        if latest is None or latest.profile_version != profile_version or latest.model != model:
            out.append(job.id)
    return out


def _store(session: Session, job: JobRow, ai: AIEvaluation, settings: Settings, profile_version: int,
           model: str, now: datetime, extra_notes: list[str]) -> JobEvaluationRow:
    score = compute(ai, settings.scoring)
    ai_dump = ai.model_dump(mode="json")
    ai_dump["_python"] = {"points": score.points, "notes": score.notes + extra_notes}
    fields = dict(
        job_id=job.id, overall_score=score.overall, category=score.category,
        potentially_ineligible=score.potentially_ineligible, eligibility_result=score.eligibility,
        professional_suitability=score.professional_suitability,
        career_attractiveness=score.career_attractiveness, cv_profile=ai.best_cv_profile,
        strengths=ai.best_matching_experience + ai.technical_skill_matches,
        missing_qualifications=ai.missing_skills,
        mandatory_requirements=[r.model_dump(mode="json") for r in ai.requirements if r.kind == "mandatory"],
        potential_obstacles=ai.potential_obstacles, recommendation=score.recommendation.value,
        explanation=ai.explanation, ai_output=ai_dump, model=model, profile_version=profile_version,
        content_hash=job.content_hash, evaluated_at=now,
    )
    # Same job + text + profile version + model (a forced re-evaluation): replace in place.
    row = session.scalar(select(JobEvaluationRow).where(
        JobEvaluationRow.job_id == job.id, JobEvaluationRow.content_hash == job.content_hash,
        JobEvaluationRow.profile_version == profile_version, JobEvaluationRow.model == model))
    if row is None:
        row = JobEvaluationRow(**fields)
        session.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    job.status = JobStatus.EVALUATED.value
    return row


def _from_cache(session: Session, job: JobRow, profile_version: int, model: str) -> JobEvaluationRow | None:
    return session.scalar(select(JobEvaluationRow).where(
        JobEvaluationRow.content_hash == job.content_hash,
        JobEvaluationRow.profile_version == profile_version,
        JobEvaluationRow.model == model).order_by(JobEvaluationRow.id.desc()).limit(1))


def evaluate_jobs(engine: Engine, settings: Settings, profile: MasterProfile, llm: LLM | None, *,
                  limit: int | None = None, job_ids: list[int] | None = None, dry_run: bool = False,
                  force: bool = False, refresh: bool = False, log: logging.Logger | None = None,
                  now: datetime | None = None) -> EvalSummary:
    """Default: only NEW jobs (status 'relevant') are evaluated - user rule 2026-11-16.
    refresh=True also re-evaluates jobs evaluated with an older profile version or another model.
    force=True ignores the cache (e.g. after the instructions changed)."""
    log = log or logging.getLogger("vjh.evaluate")
    now = now or datetime.now()
    model = settings.llm.screening_model
    backend = settings.llm.backend
    purpose = PURPOSES[backend]
    # Subscription calls are not billed per call: record $0 and limit by job count instead.
    price = settings.llm.price_for(model) if backend == "api" else None
    known_ids = {e.id for e in profile.evidence}
    system = build_system_prompt(profile, settings)
    summary = EvalSummary()

    with Session(engine) as s:
        q = select(JobRow.id)
        if job_ids:
            q = q.where(JobRow.id.in_(job_ids))
        else:
            q = q.where(JobRow.status == JobStatus.RELEVANT.value)
        ids = list(s.scalars(q.order_by(JobRow.discovered_at.desc(), JobRow.id)))
        if not job_ids and refresh:
            ids += outdated_evaluations(s, profile.profile_version, model)
        summary.outdated = len(outdated_evaluations(s, profile.profile_version, model)) if not refresh else 0
    summary.pending = len(ids)
    if limit is not None:
        ids = ids[:limit]
    if dry_run:
        return summary

    with Session(engine) as s, s.begin():
        run = RunRow(started_at=now, status="running", errors=[], log_path=None)
        s.add(run)
        s.flush()
        summary.run_id = run.id

    for job_id in ids:
        with Session(engine) as s, s.begin():
            job = s.get(JobRow, job_id)
            cached = None if force else _from_cache(s, job, profile.profile_version, model)
            if cached is not None:
                if cached.job_id != job.id:
                    copy = JobEvaluationRow(**{c.name: getattr(cached, c.name)
                                               for c in JobEvaluationRow.__table__.columns if c.name != "id"})
                    copy.job_id = job.id
                    s.add(copy)
                job.status = JobStatus.EVALUATED.value
                summary.from_cache += 1
                continue
            if len(job.description or "") > MAX_AD_CHARS:
                summary.skipped.append(f"#{job.id} text too long ({len(job.description)} chars)")
                continue
            if failures_for(s, job, model) >= settings.llm.max_failures_per_job:
                summary.skipped.append(f"#{job.id} failed {settings.llm.max_failures_per_job} times before")
                continue
            if backend == "api" and spent_today(s, now) >= settings.llm.daily_cost_cap_usd:
                summary.stopped_by_cap = True
                break
            if backend == "claude_code" and calls_today(s, now, purpose) >= settings.llm.subscription_daily_max_jobs:
                summary.stopped_by_cap = True
                break
            if llm is None:
                raise RuntimeError("no LLM client")
            user_msg = build_user_message(job)

        # --- the API call happens outside any open transaction ---
        error = None
        result = None
        try:
            result = llm.complete_json(system=system, user=user_msg, schema=evaluation_schema(list(settings.candidate.cv_strategies)), model=model)
        except BackendUnavailable as exc:
            summary.stopped_reason = str(exc)       # nothing was evaluated, nothing to record
            log.warning("evaluation stopped: %s", exc)
            break
        except Exception as exc:  # noqa: BLE001 - recorded, run continues
            error = f"API error: {type(exc).__name__}: {exc}"

        with Session(engine) as s, s.begin():
            job = s.get(JobRow, job_id)
            cost = 0.0
            if result is not None and backend == "api":
                cost = estimate_cost(result, settings.llm.price_for(result.model) or price)
            ai = None
            notes: list[str] = []
            if result is not None and error is None:
                if result.stop_reason == "refusal":
                    error = f"model declined (category: {result.refusal_category})"
                elif result.stop_reason == "max_tokens":
                    error = "answer cut off (max_tokens)"
                else:
                    try:
                        raw, notes = clean_evidence(json.loads(result.text), known_ids)
                        ai = AIEvaluation.model_validate(raw)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        error = f"invalid output discarded: {str(exc)[:300]}"
            s.add(ApiUsageRow(run_id=summary.run_id, job_id=job.id, called_at=datetime.now(),
                              model=result.model if result else model, purpose=purpose,
                              input_tokens=(result.input_tokens + result.cache_write_tokens
                                            + result.cache_read_tokens) if result else 0,
                              output_tokens=result.output_tokens if result else 0,
                              estimated_cost_usd=cost, success=error is None))
            summary.cost_usd += cost
            if error:
                summary.failed += 1
                summary.notes.append(f"#{job.id} {job.title[:50]}: {error}")
                log.warning("evaluation of job %s failed: %s", job.id, error)
                continue
            # cache under the requested model, so a fallback answer is still reused
            _store(s, job, ai, settings, profile.profile_version, model, now,
                   notes + ([f"served by {result.model}"] if result.model != model else []))
            summary.evaluated += 1

    with Session(engine) as s, s.begin():
        run = s.get(RunRow, summary.run_id)
        run.finished_at = datetime.now()
        run.jobs_evaluated = summary.evaluated + summary.from_cache
        run.api_calls = summary.evaluated + summary.failed
        run.estimated_cost_usd = round(summary.cost_usd, 6)
        run.errors = [{"error": n} for n in summary.notes]
        run.status = "ok" if not summary.failed else "partial"
    return summary
