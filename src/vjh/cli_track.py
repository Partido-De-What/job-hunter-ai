"""CLI for Phase 5: vjh track ... and vjh feedback ..."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cli_pipeline import _fail, _ok, _warn
from .config import AppConfig, load_yaml
from .db import migrate
from .db.tables import ApplicationRow, GeneratedDocumentRow, JobEvaluationRow, JobRow
from .models.common import ApplicationStatus
from .tracker.feedback import add_feedback, build_report, dismiss, save_report
from .tracker.service import TrackerError, due_items, history, set_status, sync_documents, sync_recommendations

STATUSES = [s.value for s in ApplicationStatus]


def add_parsers(sub) -> None:
    t = sub.add_parser("track", help="Application tracker")
    ts = t.add_subparsers(dest="track_cmd", required=True)
    ls = ts.add_parser("list", help="List applications")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--all", action="store_true", help="Include archived and rejected")
    sh = ts.add_parser("show", help="One application with its full history")
    sh.add_argument("job_id", type=int)
    st = ts.add_parser("set", help="Change the status of an application")
    st.add_argument("job_id", type=int)
    st.add_argument("status", choices=STATUSES)
    st.add_argument("--date", help="Date of the event (YYYY-MM-DD), default today")
    st.add_argument("--note", default="")
    st.add_argument("--interview", help='Interview date and time, e.g. "2026-11-20 10:00"')
    st.add_argument("--follow-up", help="Follow-up date (YYYY-MM-DD)")
    st.add_argument("--response", help="What the employer said")
    dm = ts.add_parser("dismiss", help="Archive a job you do not want, with the reason")
    dm.add_argument("job_id", type=int)
    dm.add_argument("reason")
    du = ts.add_parser("due", help="Follow-ups, interviews and deadlines coming up")
    du.add_argument("--days", type=int, default=7)
    ts.add_parser("sync", help="Apply the automatic status steps to existing jobs and documents")

    f = sub.add_parser("feedback", help="Feedback on recommendations")
    fs = f.add_subparsers(dest="feedback_cmd", required=True)
    fa = fs.add_parser("add", help="Was this recommendation right for you?")
    fa.add_argument("job_id", type=int)
    g = fa.add_mutually_exclusive_group(required=True)
    g.add_argument("--good", action="store_true", help="A good recommendation")
    g.add_argument("--bad", action="store_true", help="Not a good recommendation")
    fa.add_argument("reason")
    fs.add_parser("report", help="Patterns in your feedback, with suggestions (changes nothing)")

    d = sub.add_parser("dashboard", help="Open the local dashboard in your browser")
    d.add_argument("--port", type=int, default=8501)


def cmd_dashboard(cfg: AppConfig, port: int) -> int:
    """Start Streamlit on this computer only (127.0.0.1), without usage statistics."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    app = Path(__file__).parent / "dashboard" / "app.py"
    env = {**os.environ, "VJH_HOME": str(cfg.root)}
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.address", "127.0.0.1",
           "--server.port", str(port), "--browser.gatherUsageStats", "false", "--server.headless", "false",
           "--client.toolbarMode", "minimal"]      # hides Streamlit's 'Deploy' (publish online) button
    print(f"  Dashboard: http://127.0.0.1:{port}  (only on this computer). Stop it with Ctrl+C.")
    try:
        return subprocess.call(cmd, env=env, cwd=str(cfg.root))
    except KeyboardInterrupt:
        return 0


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def run_sync(cfg: AppConfig, engine) -> tuple[int, int]:
    with Session(engine) as s, s.begin():
        rec = sync_recommendations(s, cfg.settings.thresholds.recommend_min_score)
        moved = 0
        for job_id in set(s.scalars(select(GeneratedDocumentRow.job_id))):
            moved += sync_documents(s, job_id) is not None
    return rec, moved


def cmd_list(cfg: AppConfig, engine, status: str | None, include_all: bool) -> int:
    with Session(engine) as s:
        q = select(ApplicationRow)
        if status:
            q = q.where(ApplicationRow.status == status)
        elif not include_all:
            q = q.where(ApplicationRow.status.not_in(["archived", "rejected"]))
        apps = list(s.scalars(q))
        if not apps:
            print("  no applications yet. Evaluate jobs (vjh evaluate) or run: vjh track sync")
            return 0
        order = {v: i for i, v in enumerate(STATUSES)}
        rows = []
        for a in apps:
            job = s.get(JobRow, a.job_id)
            ev = s.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == a.job_id)
                          .order_by(JobEvaluationRow.evaluated_at.desc()).limit(1))
            rows.append((a, job, ev))
        rows.sort(key=lambda r: (-order[r[0].status], -(r[2].overall_score if r[2] else 0)))
        print(f"  {'Job':>4} {'Status':<20}{'Score':>5}  {'Next date':<16} Title | Employer")
        for a, job, ev in rows:
            nxt = ""
            if a.status == "interview" and a.interview_date:
                nxt = f"int. {a.interview_date:%Y-%m-%d}"
            elif a.status == "applied" and a.follow_up_date:
                nxt = f"f-up {a.follow_up_date}"
            elif job.deadline:
                nxt = f"dl. {job.deadline}"
            flag = " !" if ev and ev.potentially_ineligible else ""
            print(f"  {job.id:>4} {a.status:<20}{(ev.overall_score if ev else 0):>5.0f}  {nxt:<16} "
                  f"{job.title[:42]} | {job.employer[:24]}{flag}")
        print("\n  ! = a mandatory requirement is not met.  Details: vjh track show <job>")
    return 0


def cmd_show(engine, job_id: int) -> int:
    with Session(engine) as s:
        job = s.get(JobRow, job_id)
        if job is None:
            _fail(f"no job #{job_id}")
            return 1
        app = s.scalar(select(ApplicationRow).where(ApplicationRow.job_id == job_id))
        print(f"\n  #{job.id} {job.title}\n  {job.employer} | {job.location or '-'}\n  {job.url or ''}")
        if app is None:
            print("  No application record yet.")
            return 0
        print(f"\n  Status: {app.status.upper()}")
        for label, val in (("Discovered", job.discovered_at.date()), ("Deadline", job.deadline),
                           ("Documents prepared", app.date_prepared), ("Approved", app.date_approved),
                           ("Applied", app.date_submitted), ("Follow-up", app.follow_up_date),
                           ("Interview", app.interview_date), ("Employer response", app.employer_response),
                           ("Result", app.result)):
            if val:
                print(f"  {label + ':':<20}{val}")
        if app.documents_submitted:
            docs = [s.get(GeneratedDocumentRow, i) for i in app.documents_submitted]
            print("  Documents submitted:")
            for d in docs:
                if d:
                    print(f"    #{d.id} {d.doc_type} ({d.language}) v{d.version}: {d.file_path}")
        if app.notes:
            print("  Notes:\n" + "\n".join(f"    {ln}" for ln in app.notes.splitlines()))
        print("\n  HISTORY")
        for e in history(s, job_id):
            print(f"    {e.occurred_at:%Y-%m-%d %H:%M}  {e.from_status or '-':<20} -> {e.to_status:<20} {e.note}")
    return 0


def cmd_set(engine, args) -> int:
    try:
        with Session(engine) as s, s.begin():
            ch = set_status(s, args.job_id, args.status, note=args.note, when=_d(args.date),
                            interview_at=_dt(args.interview), follow_up=_d(args.follow_up), response=args.response)
            follow = ch.app.follow_up_date
    except (TrackerError, ValueError) as exc:
        _fail(str(exc))
        return 1
    _ok(f"job #{args.job_id}: {ch.from_status} -> {ch.to_status}")
    if args.status == "applied" and follow:
        print(f"         follow-up reminder set for {follow} (change with --follow-up)")
    for w in ch.warnings:
        _warn(w)
    return 0


def cmd_due(engine, days: int) -> int:
    today = date.today()
    with Session(engine) as s:
        items = due_items(s, today, days)
    if not items:
        _ok(f"nothing due in the next {days} days")
        return 0
    label = {"follow_up": "Follow up", "interview": "Interview", "deadline": "Deadline"}
    for i in items:
        when = "OVERDUE " if i.when < today else ""
        print(f"  {when}{i.when}  {label[i.kind]:<10} #{i.job_id} {i.title[:45]} | {i.employer[:25]} ({i.status})")
    return 0


def dispatch(cfg: AppConfig, args) -> int | None:
    if args.command == "dashboard":
        if migrate.current_revision(cfg.db_path) != migrate.head_revision():
            _fail("Database missing or out of date. Run: vjh init")
            return 1
        return cmd_dashboard(cfg, args.port)
    if args.command not in ("track", "feedback"):
        return None
    if migrate.current_revision(cfg.db_path) != migrate.head_revision():
        _fail("Database missing or out of date. Run: vjh init")
        return 1
    engine = migrate.make_engine(cfg.db_path)
    try:
        if args.command == "track":
            if args.track_cmd == "list":
                return cmd_list(cfg, engine, args.status, args.all)
            if args.track_cmd == "show":
                return cmd_show(engine, args.job_id)
            if args.track_cmd == "set":
                return cmd_set(engine, args)
            if args.track_cmd == "due":
                return cmd_due(engine, args.days)
            if args.track_cmd == "sync":
                rec, docs = run_sync(cfg, engine)
                _ok(f"{rec} job(s) marked recommended, {docs} application(s) updated from documents")
                return 0
            try:
                with Session(engine) as s, s.begin():
                    dismiss(s, args.job_id, args.reason)
            except TrackerError as exc:
                _fail(str(exc))
                return 1
            _ok(f"job #{args.job_id} archived; reason saved as feedback")
            return 0
        if args.feedback_cmd == "add":
            try:
                with Session(engine) as s, s.begin():
                    add_feedback(s, args.job_id, args.good, args.reason)
            except TrackerError as exc:
                _fail(str(exc))
                return 1
            _ok("feedback saved. See patterns with: vjh feedback report")
            return 0
        terms = load_yaml(cfg.root / "config" / "search_terms.yaml")
        excluded = terms.get("relevance", {}).get("exclude_title_patterns", [])
        with Session(engine) as s:
            rep = build_report(s, cfg.settings.thresholds.recommend_min_score, excluded)
        print(rep.to_markdown())
        print(f"  Saved: {save_report(rep, cfg.data_dir / 'reports')}")
        return 0
    finally:
        engine.dispose()
