"""Phase 7: daily routine, auto documents, report, email and Task Scheduler (all faked)."""

import subprocess
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import EXAMPLE_ROOT
from sqlalchemy.orm import Session

from vjh.automation import schedule
from vjh.automation.daily import pick_auto_documents, run_daily
from vjh.automation.notify import MailConfig, NotifyError, build_message, mail_config, send
from vjh.automation.report import StepResult, build_daily_report, render_html
from vjh.config import load_config
from vjh.db import migrate
from vjh.db.tables import ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow

NOW = datetime(2026, 11, 16, 7, 30)
SETTINGS = load_config(EXAMPLE_ROOT).settings


def add_job(s, title, score, *, inel=False, desc="We are looking for you and your lab skills in our team. " * 5,
            url="https://example.org/job", found=NOW):
    j = JobRow(title=title, employer="Lab GmbH", location="Wien", source="Test", discovered_at=found,
               retrieved_at=found, description=desc, status="evaluated", content_hash=title, url=url)
    s.add(j)
    s.flush()
    s.add(JobEvaluationRow(job_id=j.id, overall_score=score, category="strong", potentially_ineligible=inel,
                           eligibility_result="eligible", cv_profile="B", recommendation="apply", ai_output={},
                           model="m", profile_version=1, content_hash=title, evaluated_at=found, strengths=[],
                           missing_qualifications=[], mandatory_requirements=[], potential_obstacles=[]))
    s.flush()
    return j


@pytest.fixture
def s(db_path):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as session:
        yield session
    engine.dispose()


# ---------------- auto documents ----------------

def test_pick_auto_documents(s):
    start = datetime.combine(NOW.date(), datetime.min.time())
    best = add_job(s, "Best", 92)
    de = add_job(s, "Deutsch", 85, desc="Wir suchen eine Person für unser Labor und die Analytik mit der Firma. " * 5)
    add_job(s, "Ineligible", 95, inel=True)
    add_job(s, "Low", 60)
    old = add_job(s, "Old", 90, found=NOW - timedelta(days=3))
    with_docs = add_job(s, "Has docs", 88)
    s.add(GeneratedDocumentRow(job_id=with_docs.id, doc_type="cv", language="en", file_path="x", created_at=NOW,
                               version=1, approval_status="draft", evidence_ids=[]))
    applied = add_job(s, "Applied", 99)
    s.add(ApplicationRow(job_id=applied.id, status="applied", documents_submitted=[], notes=""))
    s.flush()
    picks = pick_auto_documents(s, 80, 5, start)
    assert picks == [(best.id, "en"), (de.id, "de")]
    assert pick_auto_documents(s, 80, 1, start) == [(best.id, "en")]                  # daily limit
    assert old.id not in [p[0] for p in picks]


# ---------------- report ----------------

def test_report_escapes_untrusted_content_and_unsafe_links(s):
    add_job(s, "<script>alert(1)</script> Technician", 90, url="javascript:alert(1)")
    rep = build_daily_report(s, NOW, SETTINGS, steps=[StepResult("Collect", True), StepResult("Email", False, "x")])
    html = render_html(rep)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html
    assert rep.has_news and "1 good match" in rep.subject and "errors" in rep.subject


def test_quiet_day_has_no_news(s):
    rep = build_daily_report(s, NOW, SETTINGS, steps=[StepResult("Collect", True)])
    assert not rep.has_news and "no new good matches" in rep.subject


# ---------------- email ----------------

def test_mail_config_rules():
    assert mail_config({}) is None
    assert mail_config({"SMTP_HOST": "smtp.example.com", "SMTP_USER": "me@example.com"}) is None  # no password yet
    with pytest.raises(NotifyError, match="missing"):
        mail_config({"SMTP_HOST": "smtp.example.com", "SMTP_PASSWORD": "pw"})
    base = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "me@example.com", "SMTP_PASSWORD": "pw"}
    assert mail_config(base).to == "me@example.com" and mail_config(base).port == 587
    with pytest.raises(NotifyError, match="unencrypted"):
        mail_config({**base, "SMTP_PORT": "25"})
    with pytest.raises(NotifyError, match="exactly one"):
        mail_config({**base, "NOTIFY_TO": "me@example.com, boss@company.at"})
    assert "pw" not in repr(mail_config(base))


class FakeSMTP:
    sent = []

    class _Conn:
        def __init__(self, *a, **k):
            self.tls = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            self.tls = True

        def login(self, u, p):
            pass

        def send_message(self, msg, to_addrs=None):
            assert self.tls or isinstance(self, FakeSMTP.SMTP_SSL)
            FakeSMTP.sent.append((msg, to_addrs))

    class SMTP(_Conn):
        pass

    class SMTP_SSL(_Conn):
        pass


def test_email_has_no_attachments_and_one_recipient(s):
    add_job(s, "Technician", 90)
    rep = build_daily_report(s, NOW, SETTINGS)
    mc = MailConfig("smtp.example.com", 587, "me@example.com", "pw", "me@example.com")
    msg = build_message(rep, mc)
    assert [p.get_content_type() for p in msg.iter_attachments()] == []
    assert {p.get_content_type() for p in msg.walk()} >= {"text/plain", "text/html"}
    FakeSMTP.sent.clear()
    send(msg, mc, smtp=FakeSMTP)
    assert FakeSMTP.sent[0][1] == ["me@example.com"]


# ---------------- the routine ----------------

class Cfg:
    def __init__(self, root, db_path, auto_docs=True):
        self.root, self.db_path, self.data_dir = root, db_path, root / "data"
        self.settings = SETTINGS.model_copy(deep=True)
        self.settings.automation.auto_documents = auto_docs


def test_failing_step_does_not_stop_routine(tmp_path, db_path, monkeypatch):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as ses, ses.begin():
        add_job(ses, "Great job", 91)
    engine.dispose()
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    generated = []

    def boom():
        raise RuntimeError("network down")

    res = run_daily(Cfg(tmp_path, db_path), collect=boom, filter_=lambda: 0, evaluate=lambda: 0,
                    generate_docs=lambda job_id, langs: generated.append((job_id, langs)) or 0,
                    backend_ready=lambda: (True, "ok"), now=NOW)
    names = {st.name: st for st in res.steps}
    assert not names["Collect"].ok and "network down" in names["Collect"].detail
    assert names["Evaluate (AI)"].ok and generated == [(1, ["en"])]
    assert res.report_path.exists() and "not set up" in names["Email"].detail
    assert not res.ok and not res.emailed


def test_backend_unavailable_skips_ai_steps(tmp_path, db_path, monkeypatch):
    migrate.upgrade(db_path)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    FakeSMTP.sent.clear()
    called = []
    res = run_daily(Cfg(tmp_path, db_path), collect=lambda: 0, filter_=lambda: 0,
                    evaluate=lambda: called.append("eval") or 0, generate_docs=lambda *a: called.append("docs") or 0,
                    backend_ready=lambda: (False, "Claude Code is not logged in"), now=NOW, smtp=FakeSMTP)
    assert called == []
    steps = {st.name: st for st in res.steps}
    assert "not logged in" in steps["Evaluate (AI)"].detail
    assert res.emailed and len(FakeSMTP.sent) == 1                       # failure is news: email sent


# ---------------- housekeeping ----------------

def test_housekeeping_removes_only_old_logs_and_surplus_backups(tmp_path):
    import os
    import time

    from vjh.automation.housekeeping import clean
    logs = tmp_path / "logs"
    logs.mkdir()
    old, new = logs / "daily-old.log", logs / "daily-new.log"
    old.write_text("x")
    new.write_text("x")
    keep_me = logs / "notes.txt"
    keep_me.write_text("not a log")
    os.utime(old, (time.time() - 100 * 86400,) * 2)
    os.utime(keep_me, (time.time() - 100 * 86400,) * 2)
    db = tmp_path / "vjh.db"
    db.write_text("db")
    for i in range(4):
        b = tmp_path / f"vjh.db.bak-2026111{i}"
        b.write_text("b")
        os.utime(b, (time.time() - (10 - i) * 86400,) * 2)
    other = tmp_path / "vjh.db.sandbox-stale"
    other.write_text("s")
    assert clean(logs, db, log_days=90, backups_keep=2) == (1, 2)
    assert not old.exists() and new.exists() and keep_me.exists() and db.exists() and other.exists()
    assert sorted(p.name for p in tmp_path.glob("vjh.db.bak-*")) == ["vjh.db.bak-20261112", "vjh.db.bak-20261113"]


# ---------------- Task Scheduler ----------------

def test_task_xml_is_safe_and_valid(tmp_path):
    xml = schedule.task_xml(time_hhmm="07:30", project_root=Path(r"C:\Users\me\Jobs & Co"),
                            python=Path(r"C:\proj\.venv\Scripts\pythonw.exe"), today=date(2026, 11, 16))
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:StartBoundary", ns).text == "2026-11-17T07:30:00"
    assert root.find(".//t:LogonType", ns).text == "InteractiveToken"          # runs as the user, logged on
    assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"            # no admin rights
    assert root.find(".//t:StartWhenAvailable", ns).text == "true"            # catch up missed runs
    assert root.find(".//t:MultipleInstancesPolicy", ns).text == "IgnoreNew"  # never overlaps
    assert root.find(".//t:Arguments", ns).text == "-m vjh.cli run-daily --log"
    assert root.find(".//t:WorkingDirectory", ns).text == r"C:\Users\me\Jobs & Co"


def test_schtasks_commands(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "/Query" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Status:   Ready\nNext Run Time:   17.11.2026 07:30:00\n"
                                                              "Last Result:   0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="SUCCESS", stderr="")

    schedule.install("VJH", "07:30", tmp_path, tmp_path, runner=runner)
    assert calls[0][:3] == ["schtasks", "/Create", "/TN"] and "/XML" in calls[0] and "/F" in calls[0]
    assert (tmp_path / "vjh-daily-task.xml").read_text(encoding="utf-16").startswith("<?xml")
    st = schedule.status("VJH", runner=runner)
    assert st == {"status": "Ready", "next_run": "17.11.2026 07:30:00", "last_result": "0"}
    italian = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stderr="", stdout=(  # noqa: E731
        "Prossima esecuzione:   17/11/2026 07:30:00\nStato:   Pronta\nUltima esecuzione:   30/11/1999 00:00:00\n"
        "Ultimo esito:   267011\n"))
    assert schedule.status("VJH", runner=italian) == {"next_run": "17/11/2026 07:30:00", "status": "Pronta",
                                                      "last_run": "30/11/1999 00:00:00", "last_result": "267011"}
    assert schedule.describe_result("267011") == "267011 - has not run yet"
    assert schedule.remove("VJH", runner=runner)
    missing = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: not found")  # noqa: E731
    assert schedule.status("VJH", runner=missing) is None
