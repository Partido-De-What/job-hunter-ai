"""Phase 5: application tracker, automatic steps, due list and feedback."""

import hashlib
from datetime import datetime, timedelta

import pytest
from conftest import PROJECT_ROOT
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vjh.db import migrate
from vjh.db.tables import ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from vjh.models.common import ApplicationStatus as S
from vjh.tracker.feedback import add_feedback, build_report, dismiss
from vjh.tracker.service import (
    TrackerError,
    auto_advance,
    due_items,
    history,
    set_status,
    sync_documents,
    sync_recommendations,
)

NOW = datetime(2026, 11, 15, 10, 0)
TODAY = NOW.date()


@pytest.fixture
def s(db_path):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


def add_job(s, title="Labortechniker", employer="Saat GmbH", score=None, rec="apply", inel=False, deadline=None):
    j = JobRow(title=title, employer=employer, source="t", discovered_at=NOW, retrieved_at=NOW, description="x",
               status="evaluated" if score is not None else "relevant", content_hash=title, deadline=deadline)
    s.add(j)
    s.flush()
    if score is not None:
        s.add(JobEvaluationRow(job_id=j.id, overall_score=score, category="c", potentially_ineligible=inel,
                               eligibility_result="e", cv_profile="A", recommendation=rec, ai_output={},
                               model="m", profile_version=1, content_hash=j.content_hash, evaluated_at=NOW,
                               strengths=[], missing_qualifications=[], mandatory_requirements=[],
                               potential_obstacles=[]))
    s.flush()
    return j


def add_doc(s, job, doc_type, lang="en", status="qa_passed", version=1):
    d = GeneratedDocumentRow(job_id=job.id, doc_type=doc_type, language=lang, file_path=f"{doc_type}.docx",
                             created_at=NOW, version=version, approval_status=status, evidence_ids=[])
    s.add(d)
    s.flush()
    return d


# ---------------- status changes ----------------

def test_applied_sets_dates_documents_and_history(s):
    job = add_job(s, score=80)
    cv = add_doc(s, job, "cv", status="approved")
    letter = add_doc(s, job, "cover_letter", status="approved")
    add_doc(s, job, "cv", status="qa_passed", version=2)                 # not approved: not submitted
    ch = set_status(s, job.id, "applied", note="sent via portal", now=NOW)
    app = ch.app
    assert app.date_submitted == TODAY and app.follow_up_date == TODAY + timedelta(days=10)
    assert app.documents_submitted == sorted([cv.id, letter.id]) and ch.warnings == []
    assert "sent via portal" in app.notes
    set_status(s, job.id, "interview", interview_at=datetime(2026, 11, 25, 9, 30), now=NOW)
    set_status(s, job.id, "offer", response="Offer received", now=NOW)
    steps = [(e.from_status, e.to_status) for e in history(s, job.id)]
    assert steps == [(None, "new"), ("new", "applied"), ("applied", "interview"), ("interview", "offer")]
    assert app.result == "offer" and app.employer_response == "Offer received"


def test_applied_without_approved_documents_warns(s):
    job = add_job(s)
    assert any("no approved documents" in w for w in set_status(s, job.id, "applied", now=NOW).warnings)


def test_unknown_status_and_unknown_job_are_rejected(s):
    job = add_job(s)
    with pytest.raises(TrackerError):
        set_status(s, job.id, "sent", now=NOW)
    with pytest.raises(TrackerError):
        set_status(s, 999, "applied", now=NOW)


def test_history_is_append_only(s):
    job = add_job(s)
    set_status(s, job.id, "under_review", now=NOW)
    s.commit()
    with pytest.raises(IntegrityError, match="append-only"):
        s.execute(text("UPDATE application_events SET note = 'x'"))
    s.rollback()


# ---------------- automation ----------------

def test_auto_advance_is_forward_only_and_never_past_approved(s):
    job = add_job(s)
    assert auto_advance(s, job.id, S.DOCUMENTS_GENERATED, "x", NOW)
    assert not auto_advance(s, job.id, S.RECOMMENDED, "backwards", NOW)
    with pytest.raises(TrackerError):
        auto_advance(s, job.id, S.APPLIED, "never automatic", NOW)
    set_status(s, job.id, "archived", now=NOW)
    assert not auto_advance(s, job.id, S.APPROVED, "x", NOW)          # final state stays


def test_sync_recommendations_threshold_and_ineligible_included(s):
    good = add_job(s, "A", score=75, rec="consider")
    apply_low = add_job(s, "B", score=55, rec="apply")
    low = add_job(s, "C", score=40, rec="skip")
    inel = add_job(s, "D", score=72, rec="consider", inel=True)
    assert sync_recommendations(s, 70, NOW) == 3
    status = {a.job_id: a.status for a in s.scalars(select(ApplicationRow))}
    assert status == {good.id: "recommended", apply_low.id: "recommended", inel.id: "recommended"}
    assert low.id not in status
    assert sync_recommendations(s, 70, NOW) == 0                        # idempotent


def test_sync_documents(s):
    job = add_job(s, score=80)
    cv = add_doc(s, job, "cv")
    assert sync_documents(s, job.id, NOW) == "documents_generated"
    cv.approval_status = "approved"
    add_doc(s, job, "cover_letter", lang="de", status="approved")       # other language: not a pair
    assert sync_documents(s, job.id, NOW) is None
    add_doc(s, job, "cover_letter", lang="en", status="approved")
    assert sync_documents(s, job.id, NOW) == "approved"
    app = s.scalar(select(ApplicationRow))
    assert app.date_approved == TODAY


# ---------------- due list ----------------

def test_due_items(s):
    a = add_job(s, "Applied job")
    set_status(s, a.id, "applied", when=TODAY - timedelta(days=12), now=NOW)        # follow-up overdue
    b = add_job(s, "Interview job")
    set_status(s, b.id, "interview", interview_at=NOW + timedelta(days=3), now=NOW)
    c = add_job(s, "Deadline job", deadline=TODAY + timedelta(days=2))
    set_status(s, c.id, "recommended", now=NOW)
    d = add_job(s, "Old deadline, already applied", deadline=TODAY + timedelta(days=1))
    set_status(s, d.id, "applied", now=NOW)
    kinds = [(i.kind, i.job_id) for i in due_items(s, TODAY, 7)]
    assert ("follow_up", a.id) in kinds and ("interview", b.id) in kinds and ("deadline", c.id) in kinds
    assert ("deadline", d.id) not in kinds
    assert due_items(s, TODAY, 7)[0].when < TODAY                         # overdue first


# ---------------- feedback ----------------

def test_feedback_needs_reason_and_dismiss_archives(s):
    job = add_job(s, score=70)
    with pytest.raises(TrackerError):
        add_feedback(s, job.id, False, "  ")
    dismiss(s, job.id, "Requires German C1", NOW)
    app = s.scalar(select(ApplicationRow))
    assert app.status == "archived" and "German C1" in app.notes


def test_report_suggests_but_changes_nothing(s):
    config_files = [PROJECT_ROOT / "config" / n for n in ("settings.yaml", "search_terms.yaml")]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in config_files]
    for i in range(3):
        j = add_job(s, f"Chemiker:in {i}", employer=f"E{i}", score=78)
        add_feedback(s, j.id, False, "too much organic chemistry", NOW)
    ok = add_job(s, "Pflanzenzüchter", score=65)
    add_feedback(s, ok.id, True, "plant breeding, exactly my field", NOW)
    rep = build_report(s, 70, ["post-?doc"])
    assert rep.rejected == 3 and rep.accepted == 1 and rep.avg_score_rejected == 78
    text_ = rep.to_markdown()
    assert "chemiker" in text_ and "exclude_title_patterns" in text_
    assert any("above your recommendation threshold" in x for x in rep.suggestions)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in config_files] == before


# ---------------- CLI ----------------

def test_track_cli(fake_project, capsys):
    from vjh.cli import main
    from vjh.config import load_config
    main(["init"])
    engine = migrate.make_engine(load_config().db_path)
    with Session(engine) as s, s.begin():
        add_job(s, score=80)
    engine.dispose()
    assert main(["track", "sync"]) == 0
    assert main(["track", "set", "1", "applied", "--note", "online form"]) == 0
    with pytest.raises(SystemExit):                                          # not a valid status
        main(["track", "set", "1", "sent"])
    capsys.readouterr()
    assert main(["track", "show", "1"]) == 0
    out = capsys.readouterr().out
    assert "recommended" in out and "applied" in out and "online form" in out
    assert main(["feedback", "add", "1", "--good", "good match"]) == 0
    assert main(["feedback", "report"]) == 0
