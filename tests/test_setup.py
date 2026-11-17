"""Setup wizard, Settings page and their helpers (no real AI, email or Windows task)."""

import json
import re

import pytest
import yaml
from pydantic import ValidationError

from vjh.config import load_config, load_profile
from vjh.llm.client import LLMResult
from vjh.models.settings import Settings
from vjh.setup import cv_import, files
from vjh.setup.search_setup import SearchAnswers, apply_search, draft_search, search_terms_doc


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete_json(self, *, system, user, schema, model):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return LLMResult(text=json.dumps(self.replies.pop(0)), stop_reason="end_turn", model=model)


EXTRACT = {
    "personal": {"full_name": "Sam Sample", "email": "sam@example.com", "phone": "+00 1", "nationalities": ["Irish"],
                 "date_of_birth": ""},
    "experiences": [
        {"employer": "Lab One", "title": "Technician", "location": "Cork", "start": "2021-03", "end": "",
         "current": True, "achievements": ["Ran PCR for diagnostics", "Kept QC records"]},
        {"employer": "Uni Lab", "title": "Intern", "location": "", "start": "2020", "end": "2020-09",
         "current": False, "achievements": []}],
    "education": [{"degree": "BSc", "field": "Biology", "institution": "Uni", "completed": "2020-06",
                   "details": ["Thesis on yeast"]}],
    "languages": [{"language": "English", "level_on_cv": "native", "certificate": ""},
                  {"language": "German", "level_on_cv": "fluent", "certificate": ""}],
    "skills": ["Excel"],
    "questions": [{"field": "general", "question": "Is the gap in 2021 a break?", "cv_says": ""}],
}

SEARCH = {
    "career_focus": "lab work in microbiology", "cv_strategies": [{"letter": "A", "description": "Lab and QC"},
                                                                  {"letter": "B", "description": "Research"}],
    "experience_strategies": [{"experience_id": "exp_1", "letters": ["A", "Z"]}],
    "home_city_names": {"en": "Cork", "de": "Cork", "it": "Cork"},
    "local_place_patterns": ["ireland", "cork", "\\bie\\b"], "foreign_place_patterns": ["united kingdom", "(broken"],
    "accept_language": "en-IE,en;q=0.9",
    "language_aliases": [{"language": "English", "words": ["english", "Englisch"]}],
    "term_lists": [{"label": "English", "terms": ["lab technician", "microbiologist"]}],
    "core_queries": [{"lang": "en", "queries": ["lab technician", "QC microbiology"]}],
    "title_patterns": ["lab\\b", "microbio", "[unclosed"], "topic_patterns": ["pcr\\b", "culture"],
    "exclude_title_patterns": ["professor"],
    "cv_conventions": {"photo": False, "date_of_birth": False, "nationality": True, "work_permit": True,
                       "driving_licence": False},
    "jobspy_location": "Cork, Ireland", "indeed_country": "Ireland",
}


# ---------------- files ----------------

def test_update_yaml_keeps_comments_and_validates(fake_project):
    path = fake_project / "config" / "settings.yaml"
    files.update_yaml(path, {"automation.daily_time": "09:15", "automation.run_days": ["mon", "fri"]},
                      validate=Settings.model_validate)
    text = path.read_text(encoding="utf-8")
    assert "# Paths: relative paths are relative to the project folder" in text or "#" in text.splitlines()[0]
    assert 'daily_time: 09:15' in text or "daily_time: '09:15'" in text or 'daily_time: "09:15"' in text
    assert re.search(r"run_days: \[mon, fri\]", text)
    assert load_config().settings.automation.run_days == ["mon", "fri"]
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):                 # invalid value: nothing written
        files.update_yaml(path, {"automation.daily_time": "25:99"}, validate=Settings.model_validate)
    assert path.read_text(encoding="utf-8") == before


def test_env_update_keeps_other_lines_and_hides_secrets(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# my comment\nANTHROPIC_API_KEY=abc\nSMTP_USER=old@example.com\n", encoding="utf-8")
    files.update_env(env, {"SMTP_USER": "me@example.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop",
                           "NOTIFY_TO": "me@example.com"}, apply_to_process=False)
    files.update_env(env, {"SMTP_PASSWORD": ""}, apply_to_process=False)        # empty = keep the saved one
    data = files.read_env(env)
    assert data["SMTP_PASSWORD"] == "abcd efgh ijkl mnop" and data["SMTP_USER"] == "me@example.com"
    assert "# my comment" in env.read_text(encoding="utf-8") and data["ANTHROPIC_API_KEY"] == "abc"
    status = files.env_status(env)
    assert status["SMTP_PASSWORD"] is True and status["ANTHROPIC_API_KEY"] is True   # never the value
    monkeypatch.delenv("NOTIFY_TO", raising=False)
    files.update_env(env, {"NOTIFY_TO": None})
    assert "NOTIFY_TO" not in files.read_env(env)


def test_uploads_never_overwrite(tmp_path):
    a = cv_import.save_upload(tmp_path, "cv.pdf", b"one")
    assert cv_import.save_upload(tmp_path, "cv.pdf", b"one") == a             # same file: reused
    b = cv_import.save_upload(tmp_path, "cv.pdf", b"two")
    assert b.name == "cv_2.pdf" and a.read_bytes() == b"one"


# ---------------- profile from the CV ----------------

def test_read_cv_and_build_profile_asks_instead_of_guessing():
    llm = FakeLLM([EXTRACT])
    extracted = cv_import.read_cv(llm, "CV text " * 50, load_settings())
    assert "Copy, do not improve" in llm.calls[0]["system"] and "<cv>" in llm.calls[0]["user"]
    prof = cv_import.build_profile(extracted, languages=[{"language": "English", "level": "native"},
                                                         {"language": "German", "level": "A2"}],
                                   personal={"full_name": "Sam Sample", "right_to_work": True})
    langs = {x["language"]: x["level"] for x in prof["languages"]}
    assert langs == {"English": "native", "German": "A2"}                       # the user's level, not 'fluent'
    exp1, exp2 = prof["experiences"]
    assert exp1["current"] is True and exp1["end"] is None and len(exp1["evidence_ids"]) == 2
    assert exp2["start"] is None                                                # '2020' is not a month
    questions = " | ".join(t["question"] for t in prof["todos"])
    assert "start 'Intern' at Uni Lab" in questions and "The CV lists no tasks" in questions
    assert "gap in 2021" in questions
    ev = {e["id"]: e for e in prof["evidence"]}
    assert ev[exp1["evidence_ids"][0]]["source"] == "cv" and ev[exp1["evidence_ids"][0]]["verification_status"] == "source_only"
    assert any(e["claim"] == "German: A2, no certificate" and e["verification_status"] == "verified" for e in ev.values())


def load_settings():
    from conftest import EXAMPLE_ROOT
    return load_config(EXAMPLE_ROOT).settings


# ---------------- search setup ----------------

def test_search_setup_writes_valid_files(fake_project):
    cfg = load_config()
    llm = FakeLLM([SEARCH])
    answers = SearchAnswers(country="Ireland", primary_locations=["Cork"], wanted_jobs="lab jobs",
                            document_languages=["en"])
    d = draft_search(llm, load_profile(cfg.profile_path), cfg.settings, answers)
    assert "lab jobs" in llm.calls[0]["user"] and "CANDIDATE PROFILE" in llm.calls[0]["system"]
    notes = apply_search(cfg.root, cfg.profile_path, d, answers)
    assert notes
    s = load_config().settings
    assert s.candidate.country == "Ireland" and s.preferences.primary_locations == ["Cork"]
    assert "(broken" not in s.candidate.foreign_place_patterns                 # broken regex dropped
    assert s.candidate.cv_strategies == {"A": "Lab and QC", "B": "Research"}
    assert s.candidate.cv_conventions.photo is False
    assert "# " in (cfg.root / "config" / "settings.yaml").read_text(encoding="utf-8")   # comments kept
    terms = yaml.safe_load((cfg.root / "config" / "search_terms.yaml").read_text(encoding="utf-8"))
    from vjh.pipeline.stage1 import Rules
    Rules.from_settings(terms, s)                                               # every pattern compiles
    assert "[unclosed" not in terms["relevance"]["title_patterns"] and terms["english"]
    sources = yaml.safe_load((cfg.root / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    js = [x for x in sources if x["collector"] == "jobspy"]
    assert all("Cork" in x["params"]["location"] for x in js)
    assert all(x.get("active") is False for x in sources
               if str(x.get("access_restrictions", "")).startswith("EXAMPLE"))
    prof = load_profile(cfg.profile_path)
    assert [x.priority_profiles for x in prof.experiences if x.id == "exp_1"] in ([["A"]], [])


def test_search_terms_need_title_patterns():
    with pytest.raises(ValueError):
        search_terms_doc({**SEARCH, "title_patterns": ["(bad"]})


# ---------------- schedule ----------------

def test_schedule_days():
    from pathlib import Path

    from vjh.automation import schedule
    daily = schedule.task_xml(time_hhmm="07:30", project_root=Path("C:/p"), python=Path("C:/py.exe"))
    assert "<ScheduleByDay>" in daily
    weekdays = schedule.task_xml(time_hhmm="07:30", project_root=Path("C:/p"), python=Path("C:/py.exe"),
                                 days=["fri", "mon", "tue", "wed", "thu"])
    assert "<ScheduleByWeek>" in weekdays and "<Monday />" in weekdays and "<Saturday />" not in weekdays
    assert schedule.describe_days(schedule.DAYS) == "every day"
    assert schedule.describe_days(["mon", "fri"]) == "on Mon, Fri"
    with pytest.raises(schedule.ScheduleError):
        schedule.schedule_xml([])


# ---------------- Settings page helpers ----------------

def test_set_languages_replaces_evidence(fake_project):
    from vjh.dashboard.setup_pages import set_languages
    from vjh.models.profile import MasterProfile
    prof = yaml.safe_load((fake_project / "personal" / "master_profile.yaml").read_text(encoding="utf-8"))
    set_languages(prof, [{"language": "English", "level": "C1", "certificate": "Cambridge C1"},
                         {"language": "Italian", "level": "native", "certificate": ""}])
    MasterProfile.model_validate(prof)
    assert [x["level"] for x in prof["languages"]] == ["C1", "native"]
    claims = [e["claim"] for e in prof["evidence"] if e["supporting_experience"] == "languages"]
    assert claims == ["English: C1, Cambridge C1", "Italian: native"]


# ---------------- dashboard pages ----------------

def _app(monkeypatch, **state):
    import vjh.cli_pipeline
    monkeypatch.setattr(vjh.cli_pipeline, "backend_ready", lambda cfg: (False, "not logged in (test)"))
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_resource.clear()
    from pathlib import Path

    from vjh.dashboard import data
    at = AppTest.from_file(str(Path(data.__file__).parent / "app.py"), default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_first_start_shows_the_wizard(fake_project, monkeypatch):
    (fake_project / "personal" / "master_profile.yaml").unlink()
    at = _app(monkeypatch)
    assert "Set up Job Hunter" in at.header[0].value
    assert any(b.label == "Sign in to Claude" for b in at.button)
    assert any("not logged in (test)" in w.value for w in at.warning)


def test_settings_page_renders_all_tabs(fake_project, monkeypatch):
    from vjh.cli import main
    main(["init"])
    at = _app(monkeypatch, page="Settings")
    assert "Settings" in at.header[0].value
    labels = [t.label for t in at.tabs]
    assert labels == ["Profile and CV", "Search", "Email", "Schedule", "AI and documents", "Dashboard"]
    assert any(x.label.startswith("Send my reports to") for x in at.text_input)
