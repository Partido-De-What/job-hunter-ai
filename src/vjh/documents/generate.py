"""Generate, render, convert and QA-check documents for one job. Never sends anything."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..db.tables import ApiUsageRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from ..llm.client import LLM, LLMResult
from ..models.common import ApprovalStatus
from ..models.profile import HomeLocation, MasterProfile
from ..models.settings import Settings
from .plan import CVPlan
from .qa import QAReport, ai_review, ats_keyword_issues, emphasis_issues, python_checks
from .render import LETTER_LAYOUTS, docx_text, pdf_pages, render_cv, render_letter, to_pdf
from .writer import write_plan

KINDS = {"cv": "cv", "letter": "cover_letter"}


@dataclass
class DocResult:
    kind: str
    lang: str
    docx: Path | None = None
    pdf: Path | None = None
    doc_id: int | None = None
    qa: QAReport | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


class _Recorder:
    """Wraps the LLM to record each call in api_usage (subscription calls cost $0)."""

    def __init__(self, llm: LLM, engine: Engine, settings: Settings, job_id: int):
        self.llm, self.engine, self.settings, self.job_id = llm, engine, settings, job_id
        self.purpose = "document"

    def complete_json(self, **kw) -> LLMResult:
        ok = False
        res = None
        try:
            res = self.llm.complete_json(**kw)
            ok = True
            return res
        finally:
            from ..llm.client import estimate_cost
            cost = 0.0
            if res is not None and self.settings.llm.backend == "api":
                cost = estimate_cost(res, self.settings.llm.price_for(res.model))
            with Session(self.engine) as s, s.begin():
                s.add(ApiUsageRow(job_id=self.job_id, called_at=datetime.now(),
                                  model=res.model if res else kw.get("model", "?"),
                                  purpose=self.purpose if self.settings.llm.backend == "api"
                                  else f"{self.purpose}_subscription",
                                  input_tokens=(res.input_tokens + res.cache_read_tokens + res.cache_write_tokens) if res else 0,
                                  output_tokens=res.output_tokens if res else 0,
                                  estimated_cost_usd=cost, success=ok))


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40] or "employer"


def next_version(s: Session, job_id: int, doc_type: str, lang: str) -> int:
    n = s.scalar(select(func.max(GeneratedDocumentRow.version)).where(
        GeneratedDocumentRow.job_id == job_id, GeneratedDocumentRow.doc_type == doc_type,
        GeneratedDocumentRow.language == lang))
    return (n or 0) + 1


def generate_for_job(engine: Engine, settings: Settings, profile: MasterProfile, home: HomeLocation | None,
                     llm: LLM, *, job_id: int, langs: list[str], kinds: list[str], out_root: Path,
                     photo: Path | None, today: date | None = None, convert_pdf=None,
                     count_pages=None) -> list[DocResult]:
    today = today or date.today()
    convert_pdf = convert_pdf or to_pdf          # looked up now, so tests can replace Word
    count_pages = count_pages or pdf_pages
    with Session(engine, expire_on_commit=False) as s:
        job = s.get(JobRow, job_id)
        if job is None:
            raise ValueError(f"no job #{job_id}")
        ev = s.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job_id)
                      .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))
        ai_eval = ev.ai_output if ev else None
        cv_profile = ev.cv_profile if ev else None
    folder = out_root / f"{job.id}-{slug(job.employer)}"
    rec = _Recorder(llm, engine, settings, job_id)
    results: list[DocResult] = []
    for lang in langs:
        cv_text: str | None = None
        for kind_key in kinds:
            doc_type = KINDS[kind_key]
            res = DocResult(kind=doc_type, lang=lang)
            results.append(res)
            try:
                rec.purpose = f"document_{kind_key}"
                plan, notes = write_plan(rec, kind="cv" if kind_key == "cv" else "letter", lang=lang, job=job,
                                         evaluation=ai_eval, cv_profile=cv_profile, profile=profile,
                                         settings=settings)
                res.notes = notes
                with Session(engine) as s:
                    version = next_version(s, job.id, doc_type, lang)
                path = folder / f"{kind_key}_{lang}_v{version}.docx"
                if isinstance(plan, CVPlan):
                    render_cv(plan, profile, home, lang, path, photo, settings.candidate.cv_conventions,
                              settings.candidate.home_city_names)
                    res.pdf, pdf_error = convert_pdf(path)
                    pages = count_pages(res.pdf) if res.pdf else None
                else:
                    # tighten the layout (no new AI call) until the letter fits one page
                    for compact in range(len(LETTER_LAYOUTS)):
                        render_letter(plan, profile, home, lang, path, job.employer, job.location, today,
                                      compact=compact, city_names=settings.candidate.home_city_names)
                        res.pdf, pdf_error = convert_pdf(path)
                        pages = count_pages(res.pdf) if res.pdf else None
                        if pages is None or pages <= 1:
                            break
                res.docx = path
                text = docx_text(path)
                report = QAReport(pages=pages)
                report.issues += python_checks(text, kind=doc_type, lang=lang, profile=profile,
                                               employer=job.employer, pages=pages, writer_notes=notes,
                                               language_aliases=settings.candidate.language_aliases)
                if pdf_error:
                    res.notes.append(pdf_error)
                rec.purpose = "document_qa"
                ai_issues, summary = ai_review(rec, text=text, other_text=cv_text if doc_type == "cover_letter" else None,
                                               kind=doc_type, lang=lang, job=job, profile=profile, settings=settings)
                report.issues += ai_issues
                report.ai_summary = summary
                report.issues += [_flag(f) for f in plan.flags]
                if isinstance(plan, CVPlan):
                    report.issues += emphasis_issues(plan)
                    report.issues += ats_keyword_issues(text, ai_eval)
                res.qa = report
                path.with_name(path.stem + "_qa.json").write_text(report.to_json(), encoding="utf-8")
                if doc_type == "cv":
                    cv_text = text
                evidence = sorted({i for i in _all_evidence(plan)})
                with Session(engine) as s, s.begin():
                    row = GeneratedDocumentRow(
                        job_id=job.id, doc_type=doc_type, language=lang, file_path=str(path),
                        created_at=datetime.now(), version=version,
                        approval_status=(ApprovalStatus.QA_PASSED if report.passed else ApprovalStatus.QA_FAILED).value,
                        evidence_ids=evidence)
                    s.add(row)
                    s.flush()
                    res.doc_id = row.id
            except Exception as exc:  # noqa: BLE001 - report per document, continue with the next
                res.error = f"{type(exc).__name__}: {exc}"
                from ..llm.claude_code import BackendUnavailable
                if isinstance(exc, BackendUnavailable):
                    return results
    return results


def _flag(text: str):
    from .qa import Issue
    return Issue("warning", "defensibility", text, source="writer")


def _all_evidence(plan) -> list[str]:
    out: list[str] = []
    if isinstance(plan, CVPlan):
        out += plan.summary.evidence_ids
        for b in plan.experience + plan.education:
            for x in b.bullets:
                out += x.evidence_ids
        for g in plan.skills:
            for x in g.items:
                out += x.evidence_ids
    else:
        for p in plan.paragraphs:
            out += p.evidence_ids
    return out
