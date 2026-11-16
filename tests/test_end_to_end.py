"""End to end: the whole chain through the real `vjh` commands, in a throw-away project,
with a fake internet, a fake Claude and a fake Word. Nothing real is touched or sent."""

import json
from pathlib import Path

import pytest
import yaml
from fakeweb import FakeWeb
from sqlalchemy import select
from sqlalchemy.orm import Session

from vjh.cli import main
from vjh.config import load_config
from vjh.db import migrate
from vjh.db.tables import ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from vjh.llm.client import LLMResult
from vjh.models.evaluation import DIMENSIONS

GOOD_AD = {"@type": "JobPosting", "title": "Laboratory Technician (PCR)", "datePosted": "2026-11-15",
           "hiringOrganization": {"name": "Plant Lab GmbH"},
           "jobLocation": {"address": {"addressLocality": "Wien", "addressCountry": "AT"}},
           "description": "<p>" + "We are looking for a laboratory technician for PCR, genotyping and plant "
                                  "breeding experiments in our team. " * 12 + "</p>"}
COOK_AD = {"@type": "JobPosting", "title": "Koch / Köchin", "hiringOrganization": {"name": "Kantine"},
           "description": "<p>" + "Mittagsmenüs und Küchenhygiene in der Kantine. " * 20 + "</p>"}


def page(posting):
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script><body>x</body></html>'


WEB = {"https://lab.example.at/jobs": '<a href="/job/1">Lab</a><a href="/job/2">Cook</a>',
       "https://lab.example.at/job/1": page(GOOD_AD), "https://lab.example.at/job/2": page(COOK_AD)}


class FakeClaude:
    """Answers evaluation, writing and review requests by looking at the requested schema."""

    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, user, schema, model):
        props = schema["properties"]
        if "sub_scores" in props:
            kind, ans = "evaluate", {
                "requirements": [{"text": "PCR experience", "quote": "PCR", "kind": "mandatory", "status": "met",
                                  "reason": "ran PCR", "evidence_ids": ["EV-LAB-001"]}],
                "sub_scores": [{"dimension": d, "points": 9, "max_points": 10, "reason": "good"} for d in DIMENSIONS],
                "best_cv_profile": "B", "best_matching_experience": ["Lab work"], "technical_skill_matches": ["PCR"],
                "missing_skills": [], "language_concerns": [], "career_development": "good",
                "potential_obstacles": [], "recommendation": "apply", "explanation": "Strong fit."}
        elif "focus_experience_ids" in props:
            kind, ans = "cv", {
                "summary": {"text": "Lab technician with PCR experience.", "evidence_ids": ["EV-LAB-001"]},
                "focus_experience_ids": ["exp_lab"],
                "experience": [{"experience_id": "exp_lab", "bullets": [
                    {"text": f"Ran PCR assays ({i})", "evidence_ids": ["EV-LAB-001"]} for i in range(4)]}],
                "education": [{"education_id": "edu_msc", "bullets": []}],
                "skills": [{"group": "Laboratory", "items": [{"text": "PCR", "evidence_ids": ["EV-LAB-001"]}]}],
                "keywords_used": ["PCR"], "flags": []}
        elif "paragraphs" in props:
            kind, ans = "letter", {
                "subject": "Application: Laboratory Technician", "salutation": "Dear Hiring Team,",
                "sign_off": "Kind regards", "flags": [], "paragraphs": [
                    {"kind": "opening", "text": "I am applying to Plant Lab GmbH as a laboratory technician.",
                     "evidence_ids": ["EV-LAB-001"]},
                    {"kind": "motivation", "text": "Your plant research interests me.", "evidence_ids": []}]}
        else:
            kind, ans = "qa", {"issues": [], "summary": "Fine."}
        self.calls.append(kind)
        return LLMResult(text=json.dumps(ans), stop_reason="end_turn", model=model, input_tokens=10, output_tokens=10)


@pytest.fixture
def project(fake_project, monkeypatch):
    root = fake_project
    (root / "config" / "sources.yaml").write_text(yaml.safe_dump({"sources": [
        {"name": "Plant Lab", "url": "https://lab.example.at/jobs", "kind": "employer", "collector": "jsonld_page",
         "automatic_supported": True, "params": {"link_pattern": r"/job/\d+"}, "access_restrictions": "test"},
        {"name": "Email alerts", "url": "file://x", "kind": "alerts", "collector": "email_alert",
         "access_restrictions": "local"}]}), encoding="utf-8")
    web = FakeWeb(WEB)
    import vjh.cli_pipeline
    import vjh.documents.generate
    import vjh.sources.runner
    claude = FakeClaude()
    monkeypatch.setattr(vjh.sources.runner, "PoliteClient", lambda settings: web.client()[0])  # fresh per run
    monkeypatch.setattr(vjh.cli_pipeline, "_llm", lambda cfg: claude)
    monkeypatch.setattr(vjh.cli_pipeline, "backend_ready", lambda cfg: (True, "fake Claude"))
    import vjh.cli_docs
    monkeypatch.setattr(vjh.cli_docs, "_llm", lambda cfg: claude)           # cli_docs has its own reference
    monkeypatch.setattr(vjh.cli_docs, "backend_ready", lambda cfg: (True, "fake Claude"))

    def fake_pdf(docx: Path):
        pdf = docx.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-fake")
        return pdf, None
    monkeypatch.setattr(vjh.documents.generate, "to_pdf", fake_pdf)
    monkeypatch.setattr(vjh.documents.generate, "pdf_pages", lambda pdf: 1)
    return root, claude, web


def test_guard_blocks_real_claude():
    import shutil

    from conftest import PROJECT_ROOT

    from vjh.config import load_config as _lc
    from vjh.llm.claude_code import ClaudeCodeLLM
    if shutil.which("claude") is None:
        pytest.skip("Claude Code CLI not installed")
    llm = ClaudeCodeLLM(_lc(PROJECT_ROOT).settings.llm)
    with pytest.raises(RuntimeError, match="TEST GUARD"):
        llm.complete_json(system="s", user="u", schema={}, model="m")


def rows(model):
    engine = migrate.make_engine(load_config().db_path)
    with Session(engine) as s:
        out = s.scalars(select(model)).all()
    engine.dispose()
    return out


def test_whole_chain(project, capsys):
    root, claude, web = project
    assert main(["init"]) == 0

    # 1. the daily routine: collect -> filter -> evaluate -> documents -> report -> email (not set up)
    assert main(["run-daily", "--log"]) == 0
    jobs = {j.title: j for j in rows(JobRow)}
    assert jobs["Laboratory Technician (PCR)"].status == "evaluated"
    assert jobs["Koch / Köchin"].status == "filtered_out"                 # never sent to the AI
    assert claude.calls.count("evaluate") == 1
    assert sorted(claude.calls) == ["cv", "evaluate", "letter", "qa", "qa"]   # every AI call was the fake
    ev = rows(JobEvaluationRow)[0]
    assert ev.overall_score == 90 and ev.recommendation == "apply"
    docs = rows(GeneratedDocumentRow)
    assert {(d.doc_type, d.language, d.approval_status) for d in docs} == {("cv", "en", "qa_passed"),
                                                                         ("cover_letter", "en", "qa_passed")}
    assert rows(ApplicationRow)[0].status == "documents_generated"
    assert list((root / "data" / "reports").glob("daily-*.html"))
    log = next((root / "data" / "logs").glob("daily-*.log")).read_text(encoding="utf-8")
    assert "Summary" in log and "not set up" in log                    # email skipped, not failed

    # 2. a second day: nothing new -> no AI calls at all (user rule: only new jobs)
    calls_before = len(claude.calls)
    requests_before = len(web.page_requests())
    assert main(["run-daily"]) == 0
    assert len(claude.calls) == calls_before
    assert web.page_requests()[requests_before:] == ["https://lab.example.at/jobs"]   # only the listing

    # 3. the user approves both documents and records the application
    for d in docs:
        assert main(["docs", "approve", str(d.id)]) == 0
    assert rows(ApplicationRow)[0].status == "approved"
    job_id = str(jobs["Laboratory Technician (PCR)"].id)
    assert main(["track", "set", job_id, "applied", "--note", "sent by me"]) == 0
    app = rows(ApplicationRow)[0]
    assert app.status == "applied" and sorted(app.documents_submitted) == sorted(d.id for d in docs)

    # 4. everything else answers without errors
    for cmd in (["status"], ["jobs"], ["job", job_id], ["track", "list"], ["track", "show", job_id],
                ["track", "due"], ["feedback", "add", job_id, "--good", "great fit"], ["feedback", "report"],
                ["docs", "list"], ["docs", "show", str(docs[0].id)], ["sources", "list"],
                ["evaluate", "--dry-run"], ["report", "daily", "--no-open"]):
        assert main(cmd) == 0, cmd
    out = capsys.readouterr().out
    assert "Nothing was sent" in out and "Traceback" not in out
