"""Phase 6: dashboard data layer and the Streamlit app (headless AppTest)."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from vjh.config import load_config
from vjh.dashboard import data
from vjh.db import migrate
from vjh.db.tables import JobEvaluationRow, JobRow, RunRow, SourceRow
from vjh.tracker.service import history

NOW = datetime(2026, 11, 16, 9, 0)
APP = Path(data.__file__).parent / "app.py"
AI = {"requirements": [{"text": "PCR", "quote": "PCR", "kind": "mandatory", "status": "met", "reason": "r",
                        "evidence_ids": ["EV-LAB-001"]}],
      "sub_scores": [{"dimension": d, "points": 1, "max_points": 5, "reason": "because"} for d in
                     ("professional_experience", "education", "technical_skills", "responsibility_alignment",
                      "language_compatibility", "location_practical", "career_development")],
      "best_cv_profile": "B", "best_matching_experience": ["Lab work"], "technical_skill_matches": ["PCR"],
      "missing_skills": ["HPLC"], "language_concerns": [], "career_development": "good",
      "potential_obstacles": [], "recommendation": "apply", "explanation": "Fits well.",
      "_python": {"points": {"education": 4.0}, "notes": []}}


def seed(session: Session) -> None:
    jobs = [
        ("Labortechniker:in", "Saat GmbH", "Wien", 82.0, False, {"amount_min": 40000, "period": "annual", "currency": "EUR"}, 40000,
         "Wir suchen eine Person für unser Labor und die Analytik mit PCR sowie Genotypisierung bei der Saat GmbH."),
        ("QC Analyst", "Pharma AG", "Tulln", 55.0, True, None, None,
         "We are looking for an analyst to join our team and the lab. You will run HPLC for our products."),
        ("Koch", "Kantine", "Wien", None, False, None, None, "short"),
    ]
    for i, (title, emp, loc, score, inel, sal, smin, desc) in enumerate(jobs, 1):
        session.add(JobRow(title=title, employer=emp, location=loc, source="Test", discovered_at=NOW,
                           retrieved_at=NOW, published_at=date(2026, 11, 10 + i), description=desc,
                           status="evaluated" if score else "filtered_out", content_hash=str(i),
                           salary=sal, salary_annual_min=smin, filter_reason=None if score else "not related"))
        session.flush()
        if score:
            session.add(JobEvaluationRow(job_id=i, overall_score=score, category="strong" if score > 80 else "low_priority",
                                         potentially_ineligible=inel, eligibility_result="eligible", cv_profile="B",
                                         recommendation="apply", ai_output=AI, model="m", profile_version=1,
                                         content_hash=str(i), evaluated_at=NOW, strengths=[],
                                         missing_qualifications=[], mandatory_requirements=[],
                                         potential_obstacles=[], professional_suitability=70.0,
                                         career_attractiveness=60.0, explanation="Fits well."))
    session.add(SourceRow(name="Test stale source", url="u", collection_method="html", automatic_supported=True, active=True,
                          access_restrictions="", last_success=NOW - timedelta(days=5)))
    session.add(SourceRow(name="Test broken source", url="u", collection_method="html", automatic_supported=True, active=True,
                          access_restrictions="", error_status="HTTP 500"))
    session.add(RunRow(started_at=NOW, status="partial", errors=[{"source": "Test broken source", "error": "HTTP 500"}],
                       jobs_collected=2))


@pytest.fixture
def db(db_path):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as s, s.begin():
        seed(s)
    yield engine
    engine.dispose()


# ---------------- data layer ----------------

def test_guess_language():
    assert data.guess_language("Wir suchen eine Person für unser Labor und die Analytik mit der Firma") == "de"
    assert data.guess_language("We are looking for an analyst to join our team and the lab") == "en"
    assert data.guess_language("PCR HPLC") == "unknown"


def test_jobs_frame_and_filters(db):
    with Session(db) as s:
        df = data.jobs_frame(s)
    assert list(df["id"])[:2] == [1, 2] and set(df["language"]) >= {"de", "en"}
    f = data.filter_jobs
    assert list(f(df, data.JobFilters(min_score=60, include_unscored=False))["id"]) == [1]
    assert list(f(df, data.JobFilters(location="tulln"))["id"]) == [2]
    assert list(f(df, data.JobFilters(min_salary=35000))["id"]) == [1]          # only stated salaries
    assert list(f(df, data.JobFilters(languages=("en",), include_unscored=False))["id"]) == [2]
    assert list(f(df, data.JobFilters(published_since=date(2026, 11, 13)))["id"]) == [3]
    assert len(f(df, data.JobFilters(text="saat"))) == 1
    assert df.loc[df["id"] == 1, "salary"].item() == "EUR 40,000+ annual"


def test_sources_and_overview(db):
    with Session(db) as s:
        src = data.sources_frame(s, NOW)
        ov = data.overview(s, NOW, load_config(Path(__file__).resolve().parents[1]).settings)
    states = dict(zip(src["source"], src["state"], strict=True))
    assert states == {"Test broken source": "failing", "Test stale source": "stale"}
    assert ov["job_status"]["evaluated"] == 2 and len(ov["source_problems"]) == 2


# ---------------- the Streamlit app, headless ----------------

@pytest.fixture
def app_env(fake_project):
    import streamlit as st

    from vjh.cli import main
    main(["init"])
    engine = migrate.make_engine(load_config().db_path)
    with Session(engine) as s, s.begin():
        seed(s)
    engine.dispose()
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _run(page: str, **state):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("page", ["Overview", "Progress", "Jobs", "Job details", "Applications", "Documents", "Sources",
                                  "Logs & errors", "Feedback", "Import a job"])
def test_every_page_renders(app_env, page):
    at = _run(page)
    assert at.header[0].value in (page, "Import a job")


def test_job_details_and_status_form(app_env):
    at = _run("Job details", job_id=1)
    assert "Labortechniker" in at.subheader[0].value
    form_select = [s for s in at.selectbox if s.label == "Set status"][0]
    form_select.set_value("under_review")
    [b for b in at.button if b.label == "Save status"][0].click()
    at.run()
    assert not at.exception
    engine = migrate.make_engine(load_config().db_path)
    with Session(engine) as s:
        assert [e.to_status for e in history(s, 1)][-1] == "under_review"
    engine.dispose()


# ---------------- progress, quick actions, desktop shortcut ----------------

def test_progress_funnel_rates_and_goal(db):
    from vjh.dashboard import actions
    today = date(2026, 11, 18)                      # Wednesday; week starts Monday 16.11
    actions.change_status(db, [1], "applied", date(2026, 11, 16))
    actions.change_status(db, [1], "interview", date(2026, 11, 17))
    actions.change_status(db, [2], "applied", date(2026, 11, 10))     # previous week
    actions.rate(db, [3], False, "Wrong field", archive=True)
    with Session(db) as s:
        p = data.progress(s, today, 70, weekly_goal=1, weeks=4)
    f = p["funnel"]
    assert f["Found"] == 3 and f["Passed the filter"] == 2 and f["Evaluated by AI"] == 2
    assert f["Recommended"] == 1 and f["Applied"] == 2 and f["Interview"] == 1 and f["Offer"] == 0
    r = p["rates"]
    assert r["responses"] == 1 and r["response_rate"] == 50 and r["interview_rate"] == 50 and r["waiting"] == 1
    assert p["this_week_applied"] == 1 and p["streak"] == 2              # this week and last week
    assert p["feedback_bad"] == 1 and list(p["weekly"]["applied"]) == [0, 0, 1, 1]
    with Session(db) as s:
        assert data.progress(s, today, 70, weekly_goal=0)["streak"] == 0


def test_quick_actions_keep_the_tracker_rules(db):
    from vjh.dashboard import actions
    from vjh.tracker.service import TrackerError
    with pytest.raises(TrackerError):                                    # a reason is required
        actions.rate(db, [1], True, actions.reason_text("", ""))
    msgs = actions.rate(db, [1, 2], True, actions.reason_text("Interesting employer", "seed breeding"))
    assert len(msgs) == 2
    msgs, warns = actions.change_status(db, [1], "applied", date(2026, 11, 16))
    assert "follow-up reminder 2026-11-26" in msgs[0] and warns          # no approved documents yet
    with Session(db) as s:
        assert [e.to_status for e in history(s, 1)][-1] == "applied"
    assert actions.reason_text("Wrong field", " too chemical ") == "Wrong field: too chemical"


def test_shortcut_install_uses_powershell_and_draws_icon(tmp_path, monkeypatch):
    import sys

    from vjh.automation import shortcut
    calls = []

    def fake_ps(script, env):
        calls.append(env)
        return r"C:\Users\x\Desktop\Job Hunter AI.lnk"
    monkeypatch.setattr(shortcut, "_powershell", fake_ps)
    monkeypatch.setattr(shortcut, "vjh_executable", lambda: Path(r"C:\proj\.venv\Scripts\vjh.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    out = shortcut.install(tmp_path / "proj", tmp_path / "data", "Job Hunter AI")
    assert out.endswith("Job Hunter AI.lnk")
    env = calls[0]
    assert env["VJH_LNK_NAME"] == "Job Hunter AI.lnk" and shortcut.link_name('a/b: "c"') == "a-b- -c-.lnk"
    assert env["VJH_LNK_TARGET"].endswith("vjh.exe") and env["VJH_LNK_DIR"] == str(tmp_path / "proj")
    assert (tmp_path / "data" / "vjh.ico").stat().st_size > 0


def test_dashboard_already_running_just_opens_browser(monkeypatch, tmp_path):
    import subprocess
    import webbrowser

    from vjh import cli_track
    opened = []
    monkeypatch.setattr(cli_track, "_port_in_use", lambda port: True)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: pytest.fail("must not start a second server"))
    assert cli_track.cmd_dashboard(load_config(Path(__file__).resolve().parents[1]), 8501) == 0
    assert opened == ["http://127.0.0.1:8501"]


def test_board_and_table_views_render(app_env):
    from vjh.dashboard import actions
    engine = migrate.make_engine(load_config().db_path)
    actions.change_status(engine, [1], "applied", date(2026, 11, 16))
    actions.change_status(engine, [2], "recommended")
    engine.dispose()
    at = _run("Applications")
    assert any("Applied" in m.value for m in at.markdown)
    assert any(b.label == "Open" for b in at.button)
    at = _run("Applications", apps_view="Table")
    assert not at.exception
    at = _run("Progress")
    assert at.metric[2].value == "1"                                     # applications sent


def test_titles_link_to_the_ad_or_a_search():
    import re
    show = lambda u: re.match(data.TITLE_LINK_DISPLAY, u).group(1)  # noqa: E731
    u = data.title_link("https://jobs.example.org/123", "Lab Technician (m/w/d)", "Acme")
    assert u.startswith("https://jobs.example.org/123#") and show(u) == "Lab Technician (m/w/d)"
    s = data.title_link("", "QC Analyst", "Pharma AG")
    assert s.startswith("https://www.google.com/search?q=QC+Analyst+Pharma+AG#") and show(s) == "🔎 QC Analyst"
    assert data.title_link("javascript:alert(1)", "X", "Y").startswith("https://www.google.com/")   # never a script link
    assert data.title_link("https://a.example/#/job/7", "T", "E") == "https://a.example/#/job/7"     # hash routes kept
    assert data.md_link("A [b]", "https://x.example/1", "E") == "[A (b)](<https://x.example/1>)"
