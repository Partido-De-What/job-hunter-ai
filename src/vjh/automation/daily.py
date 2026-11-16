"""The daily routine: collect -> filter -> evaluate -> auto documents -> report -> email.
Each step is isolated: a failing step is recorded and the next one still runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dashboard.data import guess_language
from ..db import migrate
from ..db.tables import ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from .notify import NotifyError, build_message, mail_config, send
from .report import StepResult, build_daily_report, save_report

CLOSED = {"applied", "interview", "offer", "rejected", "archived"}


def pick_auto_documents(session: Session, min_score: float, limit: int, since: datetime) -> list[tuple[int, str]]:
    """(job_id, language) for new, eligible, very good matches that have no documents yet."""
    picks: list[tuple[float, int, str]] = []
    for job in session.scalars(select(JobRow).where(JobRow.status == "evaluated", JobRow.discovered_at >= since)):
        ev = session.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job.id)
                            .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))
        if ev is None or ev.overall_score < min_score or ev.potentially_ineligible:
            continue
        app = session.scalar(select(ApplicationRow).where(ApplicationRow.job_id == job.id))
        if app is not None and app.status in CLOSED:
            continue
        if session.scalar(select(GeneratedDocumentRow.id).where(GeneratedDocumentRow.job_id == job.id).limit(1)):
            continue
        lang = job.language or guess_language(job.description)
        picks.append((ev.overall_score, job.id, "de" if lang == "de" else "en"))
    picks.sort(reverse=True)
    return [(job_id, lang) for _, job_id, lang in picks[:limit]]


@dataclass
class DailyResult:
    steps: list[StepResult] = field(default_factory=list)
    report_path: Path | None = None
    emailed: bool = False

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


def _step(result: DailyResult, name: str, fn: Callable[[], tuple[bool, str]]) -> None:
    print(f"\n=== {name} ===", flush=True)
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - one failing step never stops the routine
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    print(f"--- {name}: {'OK' if ok else 'FAILED'} {detail}".rstrip(), flush=True)
    result.steps.append(StepResult(name, ok, detail))


def run_daily(cfg, *, collect: Callable, filter_: Callable, evaluate: Callable, generate_docs: Callable,
              backend_ready: Callable, now: datetime | None = None, smtp=None) -> DailyResult:
    now = now or datetime.now()
    auto = cfg.settings.automation
    res = DailyResult()
    start = datetime.combine(now.date(), datetime.min.time())

    _step(res, "Collect", lambda: (collect() == 0, ""))
    _step(res, "Filter (Stage 1)", lambda: (filter_() == 0, ""))

    ready, why = backend_ready()

    def do_evaluate():
        if not ready:
            return False, f"skipped: {why}"
        return evaluate() == 0, ""
    _step(res, "Evaluate (AI)", do_evaluate)

    def do_docs():
        if not auto.auto_documents:
            return True, "switched off in settings"
        if not ready:
            return False, f"skipped: {why}"
        engine = migrate.make_engine(cfg.db_path)
        try:
            with Session(engine) as s:
                picks = pick_auto_documents(s, cfg.settings.thresholds.generate_documents_min_score,
                                            auto.auto_documents_max_per_day, start)
        finally:
            engine.dispose()
        if not picks:
            return True, "no new job above the document threshold"
        codes = [generate_docs(job_id, [lang]) for job_id, lang in picks]
        return all(c == 0 for c in codes), f"{len(picks)} job(s): " + ", ".join(f"#{j} ({lang})" for j, lang in picks)
    _step(res, "Documents for top matches", do_docs)

    def do_housekeeping():
        from .housekeeping import clean
        logs, backups = clean(cfg.data_dir / "logs", cfg.db_path, log_days=auto.log_retention_days,
                              backups_keep=auto.db_backups_keep)
        return True, f"removed {logs} old log(s), {backups} old database backup(s)"
    _step(res, "Housekeeping", do_housekeeping)

    report_holder: dict = {}

    def do_report():
        engine = migrate.make_engine(cfg.db_path)
        try:
            with Session(engine) as s:
                rep = build_daily_report(s, now, cfg.settings, steps=list(res.steps))
        finally:
            engine.dispose()
        report_holder["rep"] = rep
        res.report_path = save_report(rep, cfg.data_dir / "reports")
        return True, str(res.report_path)
    _step(res, "Daily report", do_report)

    def do_email():
        if not auto.email_enabled:
            return True, "switched off in settings"
        rep = report_holder.get("rep")
        if rep is None:
            return False, "no report to send"
        try:
            mc = mail_config()
        except NotifyError as exc:
            return False, str(exc)
        if mc is None:
            return True, "not set up (SMTP_* in .env) - report saved only"
        rep.steps = list(res.steps)
        if auto.email_only_when_news and not rep.has_news:
            return True, "nothing new - no email today"
        send(build_message(rep, mc), mc, **({"smtp": smtp} if smtp else {}))
        res.emailed = True
        return True, f"sent to {mc.to}"
    _step(res, "Email", do_email)
    return res
