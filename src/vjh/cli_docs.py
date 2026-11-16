"""CLI for Phase 4: vjh docs generate | list | show | approve | reject."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cli_pipeline import _fail, _llm, _ok, _warn, backend_ready
from .config import AppConfig, load_home_location, load_profile
from .db import migrate
from .db.tables import GeneratedDocumentRow, JobRow
from .documents.generate import generate_for_job
from .models.common import ApprovalStatus
from .tracker.service import sync_documents


def add_parsers(sub) -> None:
    p = sub.add_parser("docs", help="Tailored CVs and cover letters")
    ds = p.add_subparsers(dest="docs_cmd", required=True)
    g = ds.add_parser("generate", help="Write a CV and/or cover letter for one job")
    g.add_argument("job_id", type=int)
    g.add_argument("--lang", nargs="+", choices=["en", "de", "it"], default=["en"])
    g.add_argument("--type", nargs="+", choices=["cv", "letter"], default=["cv", "letter"], dest="kinds")
    ls = ds.add_parser("list", help="List generated documents")
    ls.add_argument("--job", type=int)
    sh = ds.add_parser("show", help="Show the quality report of one document")
    sh.add_argument("doc_id", type=int)
    ap = ds.add_parser("approve", help="Mark a document as approved by you")
    ap.add_argument("doc_id", type=int)
    ap.add_argument("--override", action="store_true", help="Approve even though QA failed")
    rj = ds.add_parser("reject", help="Mark a document as rejected")
    rj.add_argument("doc_id", type=int)


def _photo(cfg: AppConfig, profile) -> Path | None:
    if not profile.personal.photo_path:
        return None
    p = Path(profile.personal.photo_path)
    return p if p.is_absolute() else cfg.root / p


def cmd_generate(cfg: AppConfig, job_id: int, langs: list[str], kinds: list[str]) -> int:
    try:
        profile = load_profile(cfg.profile_path)
    except (ValidationError, FileNotFoundError) as exc:
        _fail(f"master profile problem: {exc}")
        return 1
    if profile.todos:
        _warn(f"{len(profile.todos)} open TODO question(s) in your profile - those facts stay conservative")
    home = load_home_location(cfg.home_location_path) if cfg.home_location_path.exists() else None
    ok, why = backend_ready(cfg)
    if not ok:
        _fail(why)
        return 1
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            job = s.get(JobRow, job_id)
            if job is None:
                _fail(f"no job #{job_id}")
                return 1
            print(f"  Writing for #{job.id}: {job.title} | {job.employer}")
        print(f"  Languages: {', '.join(langs)} | documents: {', '.join(kinds)} "
              f"(about 1 minute per document)")
        results = generate_for_job(engine, cfg.settings, profile, home, _llm(cfg), job_id=job_id, langs=langs,
                                   kinds=kinds, out_root=cfg.data_dir / "documents", photo=_photo(cfg, profile))
        with Session(engine) as s, s.begin():
            sync_documents(s, job_id)
    finally:
        engine.dispose()
    rc = 0
    for r in results:
        name = f"{r.kind} ({r.lang})"
        if r.error:
            _fail(f"{name}: {r.error}")
            rc = 1
            continue
        errors = [i for i in r.qa.issues if i.severity == "error"]
        warns = [i for i in r.qa.issues if i.severity == "warning"]
        status = "QA PASSED" if r.qa.passed else "QA FAILED"
        pages = f"{r.qa.pages} page(s)" if r.qa.pages else "pages unknown"
        (_ok if r.qa.passed else _warn)(f"{name}: doc #{r.doc_id} {status} - {pages}, "
                                        f"{len(errors)} error(s), {len(warns)} warning(s)")
        print(f"         {r.pdf or r.docx}")
        for i in errors[:6]:
            print(f"         ERROR [{i.category}] {i.text[:110]}")
        for n in r.notes:
            if "PDF" in n:
                print(f"         note: {n}")
    print("\n  Full reports: vjh docs show <doc id>.  Nothing has been sent anywhere.")
    print("  Read the documents yourself, then: vjh docs approve <doc id>")
    return rc


def cmd_list(cfg: AppConfig, job_id: int | None) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            q = select(GeneratedDocumentRow).order_by(GeneratedDocumentRow.job_id, GeneratedDocumentRow.id)
            if job_id:
                q = q.where(GeneratedDocumentRow.job_id == job_id)
            rows = list(s.scalars(q))
            if not rows:
                print("  no documents yet - create some with: vjh docs generate <job id>")
                return 0
            print(f"  {'Doc':>4} {'Job':>4}  {'Type':<13}{'Lang':<5}{'Ver':<4}{'Status':<11} Employer")
            for d in rows:
                job = s.get(JobRow, d.job_id)
                print(f"  {d.id:>4} {d.job_id:>4}  {d.doc_type:<13}{d.language:<5}v{d.version:<3}"
                      f"{d.approval_status:<11} {job.employer[:30] if job else '?'}")
    finally:
        engine.dispose()
    return 0


def cmd_show(cfg: AppConfig, doc_id: int) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            d = s.get(GeneratedDocumentRow, doc_id)
            if d is None:
                _fail(f"no document #{doc_id}")
                return 1
            path = Path(d.file_path)
            print(f"  #{d.id} {d.doc_type} ({d.language}) v{d.version} - {d.approval_status}")
            print(f"  Word: {path}")
            if path.with_suffix(".pdf").exists():
                print(f"  PDF:  {path.with_suffix('.pdf')}")
            qa_file = path.with_name(path.stem + "_qa.json")
            if not qa_file.exists():
                print("  (no QA report found)")
                return 0
            rep = json.loads(qa_file.read_text(encoding="utf-8"))
            print(f"  QA: {'PASSED' if rep['passed'] else 'FAILED'} | pages: {rep['pages']}")
            if rep.get("ai_summary"):
                print(f"  Reviewer: {rep['ai_summary']}")
            for i in sorted(rep["issues"], key=lambda i: i["severity"] != "error"):
                print(f"   {i['severity'].upper():<8}[{i['category']}] ({i['source']}) {i['text']}")
                if i.get("suggestion"):
                    print(f"            -> {i['suggestion']}")
    finally:
        engine.dispose()
    return 0


def cmd_set_status(cfg: AppConfig, doc_id: int, approve: bool, override: bool = False) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s, s.begin():
            d = s.get(GeneratedDocumentRow, doc_id)
            if d is None:
                _fail(f"no document #{doc_id}")
                return 1
            if approve and d.approval_status == ApprovalStatus.QA_FAILED.value and not override:
                _fail("QA failed for this document. Fix it (generate a new version) or use --override "
                      "after reading the QA report (vjh docs show).")
                return 1
            d.approval_status = (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED).value
            job_id = d.job_id
        with Session(engine) as s, s.begin():
            new_status = sync_documents(s, job_id)
    finally:
        engine.dispose()
    _ok(f"document #{doc_id} {'approved' if approve else 'rejected'}. Nothing was sent - applying is up to you.")
    if new_status == "approved":
        _ok(f"CV and cover letter approved: application for job #{job_id} is now 'approved'.")
        print(f"         After you have sent it yourself, record it with: vjh track set {job_id} applied")
    return 0


def dispatch(cfg: AppConfig, args) -> int | None:
    if args.command != "docs":
        return None
    if migrate.current_revision(cfg.db_path) != migrate.head_revision():
        _fail("Database missing or out of date. Run: vjh init")
        return 1
    if args.docs_cmd == "generate":
        return cmd_generate(cfg, args.job_id, args.lang, args.kinds)
    if args.docs_cmd == "list":
        return cmd_list(cfg, args.job)
    if args.docs_cmd == "show":
        return cmd_show(cfg, args.doc_id)
    if args.docs_cmd == "approve":
        return cmd_set_status(cfg, args.doc_id, True, args.override)
    return cmd_set_status(cfg, args.doc_id, False)
