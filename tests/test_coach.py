"""Ad warning signs, interview preparation pack and email drafts (fake Claude, temporary database)."""

import json
from datetime import date, datetime

import pytest
from conftest import PROJECT_ROOT
from sqlalchemy.orm import Session

from vjh.automation.report import build_daily_report, render_html
from vjh.config import load_config
from vjh.db import migrate
from vjh.db.tables import JobEvaluationRow, JobRow
from vjh.documents.coach import (
    prep_filename,
    render_prep,
    write_email,
    write_interview_prep,
)
from vjh.documents.render import docx_text
from vjh.llm.client import LLMResult
from vjh.models import MasterProfile
from vjh.scoring.red_flags import job_flags

SETTINGS = load_config(PROJECT_ROOT).settings.model_copy(deep=True)
TODAY = date(2026, 11, 17)


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete_json(self, *, system, user, schema, model):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return LLMResult(text=json.dumps(self.replies.pop(0)), stop_reason="end_turn", model=model)


@pytest.fixture
def profile(profile_data) -> MasterProfile:
    return MasterProfile.model_validate(profile_data)


@pytest.fixture
def engine(db_path):
    migrate.upgrade(db_path)
    eng = migrate.make_engine(db_path)
    yield eng
    eng.dispose()


def _job(s, **kw) -> JobRow:
    base = dict(title="Lab Technician (m/w/d)", employer="Saat GmbH", location="Wien", source="Test",
                discovered_at=datetime(2026, 11, 16), retrieved_at=datetime(2026, 11, 16),
                description="PCR and plant work. " * 40, status="evaluated", content_hash="h",
                salary={"amount_min": 2800, "period": "monthly", "currency": "EUR"})
    j = JobRow(**{**base, **kw})
    s.add(j)
    s.flush()
    return j


# ---------------- warning signs ----------------

def test_clean_ad_has_no_flags(engine):
    with Session(engine) as s:
        assert job_flags(s, _job(s, published_at=date(2026, 11, 10)), TODAY) == []


def test_old_reposted_ad_without_pay_is_flagged(engine):
    with Session(engine) as s:
        _job(s, title="Lab Technician", discovered_at=datetime(2026, 9, 1), content_hash="old")
        j = _job(s, published_at=date(2026, 9, 20), salary=None, deadline=date(2026, 11, 1),
                 description="Short ad. Unser Kunde sucht.")
        texts = " ".join(f.text for f in job_flags(s, j, TODAY))
        levels = {f.text.split()[0]: f.level for f in job_flags(s, j, TODAY)}
    assert "Posted 58 days ago" in texts
    assert "Seen before" in texts and "01.09.2026" in texts     # gender note ignored when matching titles
    assert "deadline (01.11.2026) has passed" in texts
    assert "GlBG" in texts and "Very short ad" in texts and "recruiting agency" in texts
    assert levels["Posted"] == "warn" and levels["No"] == "info"


def test_recent_duplicate_is_not_a_repost_and_pay_rule_only_in_austria(engine):
    with Session(engine) as s:
        _job(s, discovered_at=datetime(2026, 11, 10), content_hash="a")   # only 6 days earlier
        j = _job(s, location="Munich, Germany", salary=None)
        texts = " ".join(f.text for f in job_flags(s, j, TODAY))
    assert "Seen before" not in texts and "GlBG" not in texts


def test_daily_report_shows_warnings_for_good_matches(engine):
    with Session(engine) as s, s.begin():
        j = _job(s, published_at=date(2026, 9, 1))
        s.add(JobEvaluationRow(job_id=j.id, overall_score=85, category="strong", potentially_ineligible=False,
                               eligibility_result="eligible", cv_profile="A", recommendation="apply",
                               ai_output={}, model="m", profile_version=1, content_hash="h",
                               evaluated_at=datetime(2026, 11, 17, 7, 40)))
    with Session(engine) as s:
        rep = build_daily_report(s, datetime(2026, 11, 17, 8), SETTINGS)
    assert rep.top_matches[0]["flags"] == ["Posted 77 days ago"]
    assert "⚠ Posted 77 days ago" in render_html(rep)


# ---------------- interview preparation ----------------

PREP = {"role_summary": "Lab work on plant samples.", "employer_notes": ["Seed breeding company"],
        "likely_questions": [
            {"question": "How do you run a PCR?", "why_they_ask": "core task",
             "answer_outline": "S: lab. T: assays. A: ran PCR. R: results on time.", "evidence_ids": ["EV-LAB-001"]},
            {"question": "Have you used HPLC?", "why_they_ask": "ad", "answer_outline": "Invented story",
             "evidence_ids": ["EV-FAKE-9"]}],
        "gap_questions": [{"topic": "HPLC", "likely_question": "Any HPLC?", "honest_answer": "Not yet; learning."}],
        "questions_to_ask": ["What does training look like?"], "language_tips": ["Prepare a short German intro"],
        "checklist": ["Bring your diploma"]}


def test_interview_prep_drops_answers_without_evidence_and_renders(engine, profile, tmp_path):
    with Session(engine) as s:
        j = _job(s)
        llm = FakeLLM(PREP)
        prep, notes = write_interview_prep(llm, job=j, evaluation={"missing_skills": ["HPLC"]}, profile=profile,
                                           settings=SETTINGS)
        name = prep_filename(profile.personal.full_name, j.title, "en")
        path = render_prep(prep, profile, j, tmp_path / name)
    assert [q.question for q in prep.likely_questions] == ["How do you run a PCR?"]
    assert any("dropped" in n for n in notes)
    sys = llm.calls[0]["system"]
    assert "Language levels are exactly" in sys and "cannot browse" in sys and "do NOT claim" in llm.calls[0]["user"]
    text = docx_text(path)
    assert "How do you run a PCR?" in text and "Based on: Ran PCR assays" in text and "Invented" not in text
    assert name.endswith("_Interview_Prep_Lab_Technician.docx") and "m_w_d" not in name


def test_email_draft_uses_context_and_rejects_unknown_kinds(engine, profile):
    with Session(engine) as s:
        j = _job(s)
        llm = FakeLLM({"subject": "Lab Technician application", "body": "Dear Ms Huber, ..."})
        draft = write_email(llm, kind="thank_you", job=j, evaluation=None, profile=profile, settings=SETTINGS,
                            lang="de", context="spoke with Ms Huber")
        with pytest.raises(ValueError):
            write_email(llm, kind="apply_now", job=j, evaluation=None, profile=profile, settings=SETTINGS)
    assert draft.subject == "Lab Technician application"
    assert "thank-you" in llm.calls[0]["system"] and "German" in llm.calls[0]["system"]
    assert "spoke with Ms Huber" in llm.calls[0]["user"]


def test_pay_in_ad_text_and_customers_are_not_flagged(engine):
    with Session(engine) as s:
        j = _job(s, salary=None, employer="Fielmann Group",
                 description=("Sie beraten unsere Kunden im Geschäft. Mindestgehalt laut Kollektivvertrag "
                              "EUR 2.400 brutto, Bereitschaft zur Überzahlung. ") * 10)
        assert job_flags(s, j, TODAY) == []
        agency = _job(s, employer="Synergie Personal Austria GmbH", content_hash="x")
        assert any("recruiting agency" in f.text for f in job_flags(s, agency, TODAY))
