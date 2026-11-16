"""Run collection over all active automatic sources. One failure never stops the run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..db.tables import JobRow, RunRow, SourceRow
from ..models.settings import CollectionSettings
from ..models.source import SourceConfig, SourceRegistry
from .base import CollectContext, get_collector
from .http import PoliteClient
from .registry import sync_sources
from .store import store_jobs


@dataclass
class SourceResult:
    name: str
    found: int = 0
    new: int = 0
    skipped: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    run_id: int
    log_path: Path
    results: list[SourceResult] = field(default_factory=list)

    @property
    def new_jobs(self) -> int:
        return sum(r.new for r in self.results)

    @property
    def failed(self) -> list[SourceResult]:
        return [r for r in self.results if r.error]


def _run_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(f"vjh.run.{log_path.stem}")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def run_collection(
    engine: Engine,
    registry: SourceRegistry,
    settings: CollectionSettings,
    data_dir: Path,
    only: list[str] | None = None,
    client: PoliteClient | None = None,
    include_local: bool = True,
    search_terms: dict | None = None,
) -> RunSummary:
    """Collect from every active source with a runnable collector (automatic ones plus,
    if include_local, the local email-alert folder)."""
    now = datetime.now()
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    with Session(engine) as s, s.begin():
        sync_sources(s, registry)
        run = RunRow(started_at=now, status="running", errors=[])
        s.add(run)
        s.flush()
        run_id = run.id
        log_path = log_dir / f"collect-{now:%Y%m%d-%H%M%S}-run{run_id}.log"   # one file per run
        run.log_path = str(log_path)
    log = _run_logger(log_path)
    summary = RunSummary(run_id=run_id, log_path=log_path)

    def wanted(src: SourceConfig) -> bool:
        if only:
            return src.name.lower() in {o.lower() for o in only}
        if not src.active:
            return False
        if src.collector.value == "jobspy" and not settings.allow_jobspy:
            return False                     # JobSpy is opt-in (collection.allow_jobspy)
        if src.automatic_supported:
            return True
        return include_local and src.kind.value == "alerts" and src.collector.value == "email_alert"

    own_client = client is None
    client = client or PoliteClient(settings)
    ctx = CollectContext(client=client, settings=settings, now=now, data_dir=data_dir, log=log,
                         search_terms=search_terms or {})
    try:
        for src in filter(wanted, registry.sources):
            res = SourceResult(name=src.name)
            collector = get_collector(src.collector.value)
            log.info("source %s: collector %s", src.name, src.collector.value)
            try:
                if collector is None:
                    raise RuntimeError(f"no automatic collector for '{src.collector.value}'")
                with Session(engine) as s:
                    ctx.known_urls = set(s.scalars(
                        select(JobRow.url).where(JobRow.source == src.name, JobRow.url.is_not(None))))
                ctx.skipped_known = 0
                jobs = collector(src, ctx)
                with Session(engine) as s, s.begin():
                    res.new, res.skipped = store_jobs(s, jobs, run_id)
                res.found = len(jobs) + ctx.skipped_known
                res.skipped += ctx.skipped_known
                log.info("source %s: found %d, new %d, already seen %d",
                         src.name, res.found, res.new, res.skipped)
                if res.found == 0 and src.automatic_supported:
                    log.warning("source %s returned 0 jobs - check if the site changed", src.name)
            except Exception as exc:  # noqa: BLE001 - one source must never stop the run
                res.error = f"{type(exc).__name__}: {exc}"
                log.error("source %s FAILED: %s", src.name, res.error)
            summary.results.append(res)
            _record_source(engine, src.name, now, res)
    finally:
        if own_client:
            client.close()
        with Session(engine) as s, s.begin():
            run = s.get(RunRow, run_id)
            run.finished_at = datetime.now()
            run.jobs_collected = sum(r.new for r in summary.results)
            run.errors = [{"source": r.name, "error": r.error} for r in summary.failed]
            run.status = "ok" if not summary.failed else ("partial" if len(summary.failed) < len(summary.results) else "failed")
        log.info("run %d finished: %d new jobs, %d failed sources", run_id, summary.new_jobs, len(summary.failed))
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
    return summary


def _record_source(engine: Engine, name: str, when: datetime, res: SourceResult) -> None:
    with Session(engine) as s, s.begin():
        row = s.scalar(select(SourceRow).where(SourceRow.name == name))
        if row is None:
            return
        row.last_checked = when
        row.error_status = res.error
        if res.error is None:
            row.last_success = when
            row.last_jobs_found = res.found
