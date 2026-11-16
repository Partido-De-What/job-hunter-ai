"""CLI commands for Phase 3: filter, evaluate, jobs, job, run-daily."""

from __future__ import annotations

import logging
import sys
import textwrap

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppConfig, load_profile, load_yaml, secret_present
from .db import migrate
from .db.tables import JobEvaluationRow, JobRow
from .models.common import JobStatus
from .pipeline.evaluate import evaluate_jobs, spent_today
from .pipeline.stage1 import Rules, run_stage1

EST_COST_PER_JOB = (0.03, 0.08)   # rough USD range per evaluation with Opus 5.5, effort medium


def _ok(m): print(f"  [OK]   {m}")
def _warn(m): print(f"  [WARN] {m}")
def _fail(m): print(f"  [FAIL] {m}")


def _terms(cfg: AppConfig) -> dict:
    p = cfg.root / "config" / "search_terms.yaml"
    return load_yaml(p) if p.exists() else {}


def cmd_filter(cfg: AppConfig) -> int:
    rules = Rules.from_settings(_terms(cfg), cfg.settings)
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s, s.begin():
            res = run_stage1(s, rules)
    finally:
        engine.dispose()
    if not res.counts and not res.duplicates:
        _ok("no new jobs to filter")
    for status, n in sorted(res.counts.items()):
        _ok(f"{n:>4} {status}")
    if res.duplicates:
        _ok(f"{res.duplicates:>4} marked as duplicates of jobs from other sources")
    print("  See set-aside jobs and reasons with: vjh jobs --status filtered_out")
    return 0


def _llm(cfg: AppConfig):
    if cfg.settings.llm.backend == "claude_code":
        from .llm.claude_code import ClaudeCodeLLM
        return ClaudeCodeLLM(cfg.settings.llm)
    from .llm.client import AnthropicLLM
    return AnthropicLLM(cfg.settings.llm)


def backend_ready(cfg: AppConfig) -> tuple[bool, str]:
    """Is the configured AI backend usable? Returns (ok, explanation)."""
    llm_cfg = cfg.settings.llm
    if llm_cfg.backend == "api":
        if secret_present("ANTHROPIC_API_KEY") or secret_present("ANTHROPIC_AUTH_TOKEN"):
            return True, "Anthropic API key found"
        return False, "no ANTHROPIC_API_KEY in .env (backend: api)"
    from .llm.claude_code import BackendUnavailable, ClaudeCodeLLM
    try:
        status = ClaudeCodeLLM(llm_cfg).login_status()
    except BackendUnavailable as exc:
        return False, str(exc)
    if status.get("loggedIn"):
        return True, f"Claude Code logged in ({status.get('authMethod', 'subscription')})"
    return False, "Claude Code is not logged in. Run once in PowerShell: claude auth login"


def cmd_evaluate(cfg: AppConfig, limit: int | None, job_ids: list[int] | None, dry_run: bool,
                 force: bool = False, refresh: bool = False) -> int:
    try:
        profile = load_profile(cfg.profile_path)
    except (ValidationError, FileNotFoundError) as exc:
        _fail(f"master profile problem: {exc}")
        return 1
    llm_cfg = cfg.settings.llm
    engine = migrate.make_engine(cfg.db_path)
    try:
        if dry_run:
            summary = evaluate_jobs(engine, cfg.settings, profile, None, limit=limit, job_ids=job_ids, dry_run=True,
                                    refresh=refresh)
            with Session(engine) as s:
                spent = spent_today(s, __import__("datetime").datetime.now())
            n = summary.pending if limit is None else min(limit, summary.pending)
            print(f"  {summary.pending} job(s) waiting for evaluation; this run would evaluate {n}.")
            print(f"  Backend: {llm_cfg.backend} | model {llm_cfg.screening_model}, effort {llm_cfg.effort}")
            if llm_cfg.backend == "api":
                lo, hi = EST_COST_PER_JOB
                print(f"  Rough cost: ${n * lo:.2f}-${n * hi:.2f} (about ${lo}-${hi} per job).")
                print(f"  Spent today: ${spent:.2f} of the ${llm_cfg.daily_cost_cap_usd:.2f} daily cap.")
            else:
                with Session(engine) as s:
                    from .pipeline.evaluate import calls_today
                    used = calls_today(s, __import__("datetime").datetime.now(), "evaluate_subscription")
                print(f"  No extra bill (your Claude subscription). Used today: {used} of "
                      f"{llm_cfg.subscription_daily_max_jobs} jobs (subscription_daily_max_jobs).")
            ok, why = backend_ready(cfg)
            (_ok if ok else _warn)(why)
            return 0
        ok, why = backend_ready(cfg)
        if not ok:
            _fail(why)
            return 1
        logging.basicConfig(level=logging.WARNING, format="  [WARN] %(message)s")
        summary = evaluate_jobs(engine, cfg.settings, profile, _llm(cfg), limit=limit, job_ids=job_ids,
                                force=force, refresh=refresh)
    finally:
        engine.dispose()
    _ok(f"{summary.evaluated} evaluated, {summary.from_cache} reused from cache, {summary.failed} failed")
    for n in summary.notes:
        _warn(n)
    for n in summary.skipped:
        _warn(f"skipped {n}")
    if summary.outdated:
        print(f"  Note: {summary.outdated} older job(s) were evaluated with an earlier profile version. "
              "They are not re-evaluated automatically; to do it: vjh evaluate --refresh")
    if summary.stopped_reason:
        _warn(f"stopped early: {summary.stopped_reason}")
    if summary.stopped_by_cap:
        limit_txt = (f"daily cost cap ${llm_cfg.daily_cost_cap_usd:.2f}" if llm_cfg.backend == "api"
                     else f"daily limit of {llm_cfg.subscription_daily_max_jobs} jobs")
        _warn(f"{limit_txt} reached - the rest waits until tomorrow")
    if llm_cfg.backend == "api":
        print(f"  Estimated cost of this run: ${summary.cost_usd:.3f}")
    from .tracker.service import sync_recommendations
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s, s.begin():
            rec = sync_recommendations(s, cfg.settings.thresholds.recommend_min_score)
    finally:
        engine.dispose()
    if rec:
        _ok(f"{rec} job(s) newly recommended - see: vjh track list")
    print("  Results: vjh jobs")
    return 0 if not summary.failed else 1


def _latest_eval(s: Session, job_id: int) -> JobEvaluationRow | None:
    return s.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == job_id)
                    .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))


def cmd_jobs(cfg: AppConfig, status: str | None, min_score: float | None, top: int) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            st = status or JobStatus.EVALUATED.value
            jobs = list(s.scalars(select(JobRow).where(JobRow.status == st)))
            rows = []
            for j in jobs:
                ev = _latest_eval(s, j.id) if st == JobStatus.EVALUATED.value else None
                if min_score is not None and (ev is None or ev.overall_score < min_score):
                    continue
                rows.append((j, ev))
            rows.sort(key=lambda r: -(r[1].overall_score if r[1] else 0))
            if not rows:
                print(f"  no jobs with status '{st}'")
                return 0
            if st == JobStatus.EVALUATED.value:
                print(f"  {'ID':>4} {'Score':>5}  {'Category':<14} {'Rec':<9} {'Elig.':<6} Title | Employer")
                for j, ev in rows[:top]:
                    flag = "!" if ev.potentially_ineligible else ("?" if ev.eligibility_result == "unclear" else "ok")
                    print(f"  {j.id:>4} {ev.overall_score:>5.1f}  {ev.category:<14} {ev.recommendation:<9} "
                          f"{flag:<6} {j.title[:48]} | {j.employer[:28]}")
                print("\n  Elig.: ok = eligible, ? = unclear, ! = a mandatory requirement is not met")
            else:
                for j, _ in rows[:top]:
                    print(f"  {j.id:>4}  [{j.source[:12]}] {j.title[:50]} | {j.employer[:25]}\n"
                          f"        -> {j.filter_reason or ''}")
            print("  Details: vjh job <ID>")
    finally:
        engine.dispose()
    return 0


def cmd_job(cfg: AppConfig, job_id: int) -> int:
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s:
            j = s.get(JobRow, job_id)
            if j is None:
                _fail(f"no job #{job_id}")
                return 1
            print(f"\n  #{j.id} {j.title}\n  {j.employer} | {j.location or '-'} | {j.source}")
            print(f"  {j.url or ''}\n  Status: {j.status}" + (f" ({j.filter_reason})" if j.filter_reason else ""))
            ev = _latest_eval(s, j.id)
            if ev is None:
                print("  Not evaluated yet.")
                return 0
            ai = ev.ai_output
            print(f"\n  SCORE {ev.overall_score:.1f}/100 - {ev.category} - recommendation: {ev.recommendation.upper()}")
            print(f"  Eligibility: {ev.eligibility_result} | CV profile: {ev.cv_profile} | model: {ev.model}")
            print(f"  Professional suitability {ev.professional_suitability:.0f}% | "
                  f"career attractiveness {ev.career_attractiveness:.0f}%  (Commute time: unavailable)")
            print("\n  SUB-SCORES")
            pts = ai.get("_python", {}).get("points", {})
            for sub in ai["sub_scores"]:
                d = sub["dimension"]
                print(f"   {d:<26}{pts.get(d, 0):>5.1f} / {cfg.settings.scoring.weights[d]:g}   {sub['reason'][:90]}")
            print("\n  REQUIREMENTS  (M = mandatory, D = desirable)")
            mark = {"met": "MET", "partially_met": "PARTIAL", "not_met": "NOT MET", "unknown": "UNKNOWN"}
            for r in ai["requirements"]:
                print(f"   [{r['kind'][0].upper()}] {mark[r['status']]:<8} {r['text'][:70]}")
                if r.get("quote"):
                    print(f"              \"{r['quote'][:90]}\"")
                if r.get("evidence_ids"):
                    print(f"              evidence: {', '.join(r['evidence_ids'])}")
            for title, key in (("MISSING SKILLS", "missing_skills"), ("LANGUAGE CONCERNS", "language_concerns"),
                               ("OBSTACLES", "potential_obstacles")):
                if ai.get(key):
                    print(f"\n  {title}: " + "; ".join(ai[key]))
            print("\n  EXPLANATION\n" + textwrap.indent(textwrap.fill(ev.explanation, 90), "   "))
            for note in ai.get("_python", {}).get("notes", []):
                print(f"   note: {note}")
    finally:
        engine.dispose()
    return 0


def add_parsers(sub) -> None:
    sub.add_parser("filter", help="Stage 1: set aside irrelevant/old jobs and mark duplicates (free)")
    p = sub.add_parser("evaluate", help="Stage 2: AI evaluation of relevant jobs (costs money)")
    p.add_argument("--limit", type=int)
    p.add_argument("--job", type=int, action="append", help="Evaluate this job id (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen and the cost, call nothing")
    p.add_argument("--force", action="store_true",
                   help="Re-evaluate even if a cached result exists (use with --job)")
    p.add_argument("--refresh", action="store_true",
                   help="Also re-evaluate older jobs after your profile or the model changed (uses AI)")
    p = sub.add_parser("jobs", help="List jobs (default: evaluated, best first)")
    p.add_argument("--status", choices=[s.value for s in JobStatus])
    p.add_argument("--min-score", type=float)
    p.add_argument("--top", type=int, default=30)
    p = sub.add_parser("job", help="Show one job with its full evaluation")
    p.add_argument("id", type=int)


def dispatch(cfg: AppConfig, args, collect_fn) -> int | None:
    if args.command in {"filter", "evaluate", "jobs", "job"}:
        if migrate.current_revision(cfg.db_path) != migrate.head_revision():
            print("Database missing or out of date. Run: vjh init", file=sys.stderr)
            return 1
    if args.command == "filter":
        return cmd_filter(cfg)
    if args.command == "evaluate":
        return cmd_evaluate(cfg, args.limit, args.job, args.dry_run, args.force, args.refresh)
    if args.command == "jobs":
        return cmd_jobs(cfg, args.status, args.min_score, args.top)
    if args.command == "job":
        return cmd_job(cfg, args.id)
    return None
