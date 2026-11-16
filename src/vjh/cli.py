"""Command-line interface: `vjh init`, `vjh status`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, cli_automation, cli_docs, cli_pipeline, cli_track
from .config import AppConfig, load_config, load_home_location, load_profile, load_yaml, secret_present
from .db import migrate
from .db.tables import Base, SourceRow
from .profile.registry import sync_evidence
from .sources.http import FetchError, PoliteClient, RobotsDisallowed
from .sources.imports import ImportRefused, import_file, import_text, import_url
from .sources.investigate import check_source, write_report
from .sources.registry import load_registry, sync_sources
from .sources.runner import run_collection
from .sources.store import store_jobs

DATA_SUBDIRS = ("logs", "reports", "documents", "imports")
MUST_BE_IGNORED = ("cv/", "personal/", "data/", ".env")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def gitignore_missing(root: Path) -> list[str]:
    path = root / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    entries = {ln.strip() for ln in lines}
    return [e for e in MUST_BE_IGNORED if e not in entries]


def cmd_init(cfg: AppConfig) -> int:
    print("vjh init")
    for sub in DATA_SUBDIRS:
        (cfg.data_dir / sub).mkdir(parents=True, exist_ok=True)
    _ok(f"data folders ready in {cfg.data_dir}")

    backup = migrate.upgrade(cfg.db_path)
    if backup:
        _ok(f"backup made before upgrade: {backup}")
    _ok(f"database at schema {migrate.current_revision(cfg.db_path)}: {cfg.db_path}")

    if not cfg.profile_path.exists():
        _warn(f"no master profile at {cfg.profile_path} (nothing to sync)")
        return 0
    try:
        profile = load_profile(cfg.profile_path)
    except ValidationError as exc:
        _fail(f"master_profile.yaml is invalid:\n{exc}")
        return 1
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as session, session.begin():
            n = sync_evidence(session, profile)
    finally:
        engine.dispose()
    _ok(f"{n} evidence items synced (profile version {profile.profile_version})")
    if profile.todos:
        _warn(f"{len(profile.todos)} open TODO questions in master_profile.yaml")
    try:
        registry = load_registry(cfg.root)
    except (ValidationError, FileNotFoundError) as exc:
        _fail(f"config/sources.yaml is invalid or missing:\n{exc}")
        return 1
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as session, session.begin():
            n = sync_sources(session, registry)
    finally:
        engine.dispose()
    _ok(f"{n} sources registered ({sum(s.automatic_supported for s in registry.sources)} automatic)")
    return 0


def cmd_status(cfg: AppConfig) -> int:
    problems = 0
    print(f"Job Hunter AI {__version__}")
    print(f"Project: {cfg.root}")

    print("\nConfiguration")
    _ok("config/settings.yaml is valid")
    p, c = cfg.settings.preferences, cfg.settings.candidate
    print(f"         searching in {c.country} ({', '.join(p.primary_locations) or '-'}); "
          f"min salary {p.salary_currency} {p.min_gross_annual_salary:,.0f}/yr, "
          f"max commute {p.max_commute_minutes_one_way} min")
    if cfg.settings.collection.allow_jobspy:
        _warn("JobSpy is ON: LinkedIn/Indeed/Glassdoor are scraped against their terms (your decision)")
    missing = gitignore_missing(cfg.root)
    if missing:
        problems += 1
        _fail(f".gitignore does not protect: {', '.join(missing)}")
    else:
        _ok(".gitignore protects cv/, personal/, data/, .env")
    llm = cfg.settings.llm
    if llm.backend == "api":
        if secret_present("ANTHROPIC_API_KEY"):
            _ok("AI: Anthropic API (key is set)")
        else:
            problems += 1
            _fail("AI backend is 'api' but ANTHROPIC_API_KEY is not set in .env")
    else:
        _ok(f"AI: Claude subscription via Claude Code (model {llm.screening_model}); "
            "login check: vjh evaluate --dry-run")
    email = "set up" if secret_present("SMTP_PASSWORD") else "not set up (optional, see README section 8)"
    (_ok if secret_present("SMTP_PASSWORD") else _warn)(f"Email notifications: {email}")

    print("\nDatabase")
    current, head = migrate.current_revision(cfg.db_path), migrate.head_revision()
    print(f"         {cfg.db_path}")
    if current is None:
        problems += 1
        _fail("database not created - run: vjh init")
    elif current != head:
        problems += 1
        _fail(f"schema {current}, latest is {head} - run: vjh init")
    else:
        _ok(f"schema up to date ({current})")
        engine = migrate.make_engine(cfg.db_path)
        try:
            with Session(engine) as session:
                for table in Base.metadata.sorted_tables:
                    count = session.execute(select(func.count()).select_from(table)).scalar_one()
                    print(f"         {table.name:<22}{count:>6} rows")
        finally:
            engine.dispose()

    print("\nPersonal data")
    if cfg.profile_path.exists():
        try:
            profile = load_profile(cfg.profile_path)
            counts = Counter(e.verification_status.value for e in profile.evidence)
            _ok(f"master profile v{profile.profile_version}: {len(profile.evidence)} evidence items "
                f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})")
            if profile.todos:
                _warn(f"{len(profile.todos)} open TODO questions")
        except ValidationError as exc:
            problems += 1
            _fail(f"master_profile.yaml is invalid:\n{exc}")
    else:
        problems += 1
        _fail(f"missing {cfg.profile_path}")
    if cfg.home_location_path.exists():
        try:
            load_home_location(cfg.home_location_path)
            _ok("home location set (not shown)")
        except ValidationError:
            problems += 1
            _fail("home_location.yaml is invalid")
    else:
        _warn("home location not set (commute times unavailable)")

    print("\nSources")
    if current == head:
        rows = _source_rows(cfg)
        auto = [r for r in rows if r.automatic_supported and r.active]
        _ok(f"{len(rows)} registered, {len(auto)} collected automatically")
        for r in auto:
            if r.error_status:
                _warn(f"{r.name}: last attempt failed - {r.error_status[:100]}")
            elif r.last_success is None:
                _warn(f"{r.name}: never collected yet")
            elif datetime.now() - r.last_success > timedelta(days=STALE_DAYS):
                _warn(f"{r.name}: stale, last success {r.last_success:%Y-%m-%d}")

    print("\nOverall: " + ("OK" if problems == 0 else f"{problems} problem(s)"))
    return 0 if problems == 0 else 1


STALE_DAYS = 2


def _source_rows(cfg: AppConfig) -> list[SourceRow]:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine, expire_on_commit=False) as s:
            return list(s.scalars(select(SourceRow).order_by(SourceRow.name)))
    finally:
        engine.dispose()


def _require_db(cfg: AppConfig) -> bool:
    if migrate.current_revision(cfg.db_path) != migrate.head_revision():
        print("Database missing or out of date. Run: vjh init", file=sys.stderr)
        return False
    return True


def cmd_sources_list(cfg: AppConfig) -> int:
    if not _require_db(cfg):
        return 1
    print(f"{'Source':<36}{'Method':<16}{'Auto':<6}{'Last success':<18}Status")
    for r in sorted(_source_rows(cfg), key=lambda r: (not r.automatic_supported, r.name.lower())):
        last =f"{r.last_success:%Y-%m-%d %H:%M}" if r.last_success else "never"
        if not r.active:
            state = "inactive"
        elif r.error_status:
            state = "FAILING: " + r.error_status[:60]
        elif r.automatic_supported and r.last_success is None:
            state = "not collected yet"
        elif r.automatic_supported and datetime.now() - r.last_success > timedelta(days=STALE_DAYS):
            state = "STALE"
        elif not r.automatic_supported:
            state = "alerts/manual only"
        else:
            state = f"ok ({r.last_jobs_found} jobs last time)"
        print(f"{r.name[:35]:<36}{(r.collector or '-'):<16}{'yes' if r.automatic_supported else 'no':<6}{last:<18}{state}")
    return 0


def cmd_sources_check(cfg: AppConfig, only: list[str] | None) -> int:
    registry = load_registry(cfg.root)
    targets = [s for s in registry.sources if not only or s.name.lower() in {o.lower() for o in only}]
    results = []
    with PoliteClient(cfg.settings.collection) as client:
        for src in targets:
            print(f"  checking {src.name} ...", flush=True)
            results.append(check_source(client, src))
    report = write_report(results, cfg.data_dir / "reports")
    print()
    for r in results:
        found = ", ".join(filter(None, [
            f"{r.jobpostings} JobPosting" if r.jobpostings else "",
            "feeds: " + " ".join(r.feeds) if r.feeds else "",
            "ATS: " + ", ".join(f"{k} ({v})" for k, v in r.ats.items()) if r.ats else "",
        ])) or r.note or "nothing machine-readable found"
        print(f"  {r.name[:34]:<35} robots={r.robots:<10} http={r.http:<5} {found}")
    print(f"\nReport saved: {report}")
    return 0


def cmd_collect(cfg: AppConfig, only: list[str] | None) -> int:
    if not _require_db(cfg):
        return 1
    registry = load_registry(cfg.root)
    engine = migrate.make_engine(cfg.db_path)
    try:
        terms_path = cfg.root / "config" / "search_terms.yaml"
        terms = load_yaml(terms_path) if terms_path.exists() else {}
        summary = run_collection(engine, registry, cfg.settings.collection, cfg.data_dir, only=only,
                                 search_terms=terms)
    finally:
        engine.dispose()
    for r in summary.results:
        if r.error:
            _fail(f"{r.name}: {r.error}")
        else:
            _ok(f"{r.name}: {r.found} found, {r.new} new, {r.skipped} already seen")
    if not summary.results:
        _warn("no sources to collect (none automatic, and no source matched)")
    print(f"\nRun {summary.run_id}: {summary.new_jobs} new jobs. Log: {summary.log_path}")
    return 0 if not summary.failed else 1


def _store_one(cfg: AppConfig, cj) -> int:
    if not _require_db(cfg):
        return 1
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s, s.begin():
            new, _ = store_jobs(s, [cj], run_id=None)
    finally:
        engine.dispose()
    j = cj.job
    if new:
        _ok(f"imported: {j.title} | {j.employer} | {len(j.description)} characters of text")
    else:
        _warn(f"already in the database: {j.title} | {j.employer}")
    if len(j.description) < 200:
        _warn("very little job text was found - consider importing the text directly")
    return 0


def cmd_import_url(cfg: AppConfig, args) -> int:
    try:
        with PoliteClient(cfg.settings.collection) as client:
            cj = import_url(client, args.url, title=args.title, employer=args.employer)
    except (ImportRefused, RobotsDisallowed, FetchError) as exc:
        _fail(str(exc))
        if isinstance(exc, RobotsDisallowed):
            print("         Copy the job text into a .txt file and use: vjh import-file")
        return 1
    return _store_one(cfg, cj)


def cmd_import_file(cfg: AppConfig, args) -> int:
    path = Path(args.path)
    if not path.is_file():
        _fail(f"file not found: {path}")
        return 1
    try:
        cj = import_file(path, title=args.title, employer=args.employer, url=args.url)
    except ImportRefused as exc:
        _fail(str(exc))
        return 1
    return _store_one(cfg, cj)


def cmd_import_text(cfg: AppConfig, args) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        cj = import_text(text, title=args.title, employer=args.employer, url=args.url)
    except ImportRefused as exc:
        _fail(str(exc))
        return 1
    return _store_one(cfg, cj)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vjh", description="Job Hunter AI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create folders and database, sync evidence and sources")
    sub.add_parser("status", help="Show configuration, database, profile and source health")

    p_src = sub.add_parser("sources", help="Source registry")
    src_sub = p_src.add_subparsers(dest="sources_cmd", required=True)
    src_sub.add_parser("list", help="List sources with monitoring status")
    p_check = src_sub.add_parser("check", help="Check robots.txt and detect feeds (read-only)")
    p_check.add_argument("--source", action="append", help="Only this source (repeatable)")

    p_collect = sub.add_parser("collect", help="Collect jobs from automatic sources and saved alert emails")
    p_collect.add_argument("--source", action="append", help="Only this source (repeatable)")

    p_url = sub.add_parser("import-url", help="Import one job ad from a web address")
    p_url.add_argument("url")
    p_url.add_argument("--title")
    p_url.add_argument("--employer")

    p_file = sub.add_parser("import-file", help="Import one job ad from a .txt/.md/.html/.pdf file")
    p_file.add_argument("path")
    p_file.add_argument("--title")
    p_file.add_argument("--employer")
    p_file.add_argument("--url")

    p_text = sub.add_parser("import-text", help="Import one job ad from text ('-' reads from input)")
    p_text.add_argument("text")
    p_text.add_argument("--title", required=True)
    p_text.add_argument("--employer", required=True)
    p_text.add_argument("--url")

    cli_pipeline.add_parsers(sub)
    cli_docs.add_parsers(sub)
    cli_track.add_parsers(sub)
    cli_automation.add_parsers(sub)

    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    rc = cli_pipeline.dispatch(cfg, args, cmd_collect)
    if rc is None:
        rc = cli_docs.dispatch(cfg, args)
    if rc is None:
        rc = cli_track.dispatch(cfg, args)
    if rc is None:
        rc = cli_automation.dispatch(cfg, args)
    if rc is not None:
        return rc

    if args.command == "init":
        return cmd_init(cfg)
    if args.command == "status":
        return cmd_status(cfg)
    if args.command == "sources":
        return cmd_sources_list(cfg) if args.sources_cmd == "list" else cmd_sources_check(cfg, args.source)
    if args.command == "collect":
        return cmd_collect(cfg, args.source)
    if args.command == "import-url":
        return cmd_import_url(cfg, args)
    if args.command == "import-file":
        return cmd_import_file(cfg, args)
    return cmd_import_text(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
