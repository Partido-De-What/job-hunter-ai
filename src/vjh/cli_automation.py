"""CLI for Phase 7: vjh run-daily, vjh schedule ..., vjh report daily, vjh notify test."""

from __future__ import annotations

import sys
from datetime import datetime

from sqlalchemy.orm import Session

from .automation import schedule
from .automation.daily import run_daily
from .automation.notify import NotifyError, build_message, mail_config, send
from .automation.report import build_daily_report, save_report
from .cli_pipeline import _fail, _ok, _warn
from .config import AppConfig
from .db import migrate


def add_parsers(sub) -> None:
    rd = sub.add_parser("run-daily", help="Collect, filter, evaluate, documents, report, email (the daily task)")
    rd.add_argument("--log", action="store_true", help="Also write all output to data/logs/daily-*.log")

    sc = sub.add_parser("schedule", help="Windows Task Scheduler: run the daily routine automatically")
    ss = sc.add_subparsers(dest="schedule_cmd", required=True)
    ins = ss.add_parser("install", help="Register (or update) the daily task")
    ins.add_argument("--time", help="HH:MM, default from settings (automation.daily_time)")
    ss.add_parser("status", help="Is it installed? Next and last run")
    ss.add_parser("run-now", help="Start the scheduled task immediately")
    ss.add_parser("remove", help="Delete the daily task")

    rp = sub.add_parser("report", help="Reports")
    rs = rp.add_subparsers(dest="report_cmd", required=True)
    dly = rs.add_parser("daily", help="Write today's HTML report (and open it)")
    dly.add_argument("--email", action="store_true", help="Also email it (to NOTIFY_TO only)")
    dly.add_argument("--no-open", action="store_true")

    nt = sub.add_parser("notify", help="Email notifications")
    ns = nt.add_subparsers(dest="notify_cmd", required=True)
    ns.add_parser("test", help="Send a test email to yourself (NOTIFY_TO)")


class _Tee:
    """Write to the console (if any) and to a log file."""

    def __init__(self, console, logfile):
        self.console, self.logfile = console, logfile

    def write(self, s):
        if self.console is not None:
            try:
                self.console.write(s)
            except (OSError, ValueError):
                pass
        self.logfile.write(s)
        return len(s)

    def flush(self):
        if self.console is not None:
            try:
                self.console.flush()
            except (OSError, ValueError):
                pass
        self.logfile.flush()


def cmd_run_daily(cfg: AppConfig, log: bool) -> int:
    from .cli import cmd_collect
    from .cli_docs import cmd_generate
    from .cli_pipeline import backend_ready, cmd_evaluate, cmd_filter

    logfile = None
    old_out, old_err = sys.stdout, sys.stderr
    if log:
        (cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        path = cfg.data_dir / "logs" / f"daily-{datetime.now():%Y%m%d-%H%M%S}.log"
        logfile = open(path, "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
        sys.stdout, sys.stderr = _Tee(old_out, logfile), _Tee(old_err, logfile)
    try:
        print(f"Job Hunter daily run - {datetime.now():%Y-%m-%d %H:%M}")
        res = run_daily(cfg, collect=lambda: cmd_collect(cfg, None), filter_=lambda: cmd_filter(cfg),
                        evaluate=lambda: cmd_evaluate(cfg, None, None, False),
                        generate_docs=lambda job_id, langs: cmd_generate(cfg, job_id, langs, ["cv", "letter"]),
                        backend_ready=lambda: backend_ready(cfg))
        print("\n=== Summary ===")
        for s in res.steps:
            print(f"  {'OK    ' if s.ok else 'FAILED'} {s.name}: {s.detail}")
        if res.report_path:
            print(f"  Report: {res.report_path}")
        return 0 if res.ok else 1
    finally:
        if logfile:
            sys.stdout, sys.stderr = old_out, old_err
            logfile.close()


def cmd_schedule(cfg: AppConfig, args) -> int:
    name = cfg.settings.automation.task_name
    try:
        if args.schedule_cmd == "install":
            hhmm = args.time or cfg.settings.automation.daily_time
            if len(hhmm) != 5 or hhmm[2] != ":":
                _fail("use --time HH:MM, e.g. 07:30")
                return 1
            days = cfg.settings.automation.run_days
            schedule.install(name, hhmm, cfg.root, cfg.data_dir, days=days)
            _ok(f"task '{name}' registered for {hhmm} {schedule.describe_days(days)}")
            print("         It runs only while you are logged in; missed runs start when the PC is on again.")
            print("         Check it with: vjh schedule status   |   remove it with: vjh schedule remove")
            return 0
        if args.schedule_cmd == "remove":
            if schedule.remove(name):
                _ok(f"task '{name}' removed - the daily routine no longer runs automatically")
            else:
                _warn(f"task '{name}' was not installed")
            return 0
        if args.schedule_cmd == "run-now":
            schedule.run_now(name)
            _ok("started. It runs in the background; follow it in data\\logs\\daily-*.log or the dashboard.")
            return 0
        st = schedule.status(name)
        if st is None:
            _warn(f"task '{name}' is not installed. Install it with: vjh schedule install")
            return 1
        _ok(f"task '{name}' is installed")
        if st.get("last_result") == "267011":
            st.pop("last_run", None)                      # Windows shows 1999 before the first run
        for k, label in (("status", "Status"), ("next_run", "Next run"), ("last_run", "Last run"),
                         ("last_result", "Last result")):
            if k in st:
                value = schedule.describe_result(st[k]) if k == "last_result" else st[k]
                print(f"         {label + ':':<22}{value}")
        return 0
    except schedule.ScheduleError as exc:
        _fail(str(exc))
        return 1


def cmd_report(cfg: AppConfig, email: bool, open_it: bool) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            rep = build_daily_report(s, datetime.now(), cfg.settings)
    finally:
        engine.dispose()
    path = save_report(rep, cfg.data_dir / "reports")
    _ok(f"{rep.subject}\n         {path}")
    if email:
        rc = _email(rep)
        if rc:
            return rc
    if open_it:
        import webbrowser
        webbrowser.open(path.as_uri())
    return 0


def _email(rep) -> int:
    try:
        mc = mail_config()
        if mc is None:
            import os
            if os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"):
                _fail("almost ready: paste your Gmail app password after SMTP_PASSWORD= in the .env file "
                      "(create it at https://myaccount.google.com/apppasswords)")
            else:
                _fail("email is not set up: add SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, NOTIFY_TO to .env")
            return 1
        send(build_message(rep, mc), mc)
    except NotifyError as exc:
        _fail(str(exc))
        return 1
    _ok(f"emailed to {mc.to}")
    return 0


def cmd_notify_test(cfg: AppConfig) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            rep = build_daily_report(s, datetime.now(), cfg.settings)
    finally:
        engine.dispose()
    rep.subject_prefix = "[TEST] "
    return _email(rep)


def dispatch(cfg: AppConfig, args) -> int | None:
    if args.command not in ("run-daily", "schedule", "report", "notify"):
        return None
    if args.command != "schedule" and migrate.current_revision(cfg.db_path) != migrate.head_revision():
        _fail("Database missing or out of date. Run: vjh init")
        return 1
    if args.command == "run-daily":
        return cmd_run_daily(cfg, args.log)
    if args.command == "schedule":
        return cmd_schedule(cfg, args)
    if args.command == "report":
        return cmd_report(cfg, args.email, not args.no_open)
    return cmd_notify_test(cfg)
