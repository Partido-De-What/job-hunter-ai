"""Job Hunter AI - local Streamlit dashboard.

Start with:  vjh dashboard
Runs on 127.0.0.1 only. Every action uses the same code and rules as the CLI:
nothing is ever sent to an employer; 'applied' is always set by the user.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from vjh.config import load_config, load_home_location, load_profile, load_yaml
from vjh.dashboard import data
from vjh.dashboard import setup_pages as setup
from vjh.db import migrate
from vjh.db.tables import GeneratedDocumentRow
from vjh.models.common import ApplicationStatus, ApprovalStatus

PAGES = ["Overview", "Progress", "Jobs", "Job details", "Applications", "Documents", "Sources", "Logs & errors",
         "Feedback", "Import a job", "Settings"]
APP_STATUSES = [s.value for s in ApplicationStatus]
CATEGORY_LABEL = {"excellent": "Excellent", "strong": "Strong", "good_potential": "Good potential",
                  "possible": "Possible", "low_priority": "Low priority"}
REQ_LABEL = {"met": "✅ met", "partially_met": "🟡 partial", "not_met": "❌ not met", "unknown": "❔ unknown"}
# Menu icons (Google Material Symbols, built into Streamlit - nothing is downloaded).
PAGE_ICON = {"Overview": ":material/dashboard:", "Progress": ":material/trending_up:", "Jobs": ":material/work:",
             "Job details": ":material/description:", "Applications": ":material/view_kanban:",
             "Documents": ":material/folder_open:", "Sources": ":material/hub:", "Logs & errors": ":material/terminal:",
             "Feedback": ":material/thumbs_up_down:", "Import a job": ":material/add_link:",
             "Settings": ":material/settings:"}
# One colour per score category, from strong (green) to weak (grey).
CATEGORY_COLOR = {"excellent": "green", "strong": "green", "good_potential": "blue", "possible": "orange",
                  "low_priority": "gray"}


@st.cache_resource
def _cfg():
    return load_config()


@st.cache_resource
def _engine(db_path: str):
    return migrate.make_engine(Path(db_path))


cfg = _cfg()
st.set_page_config(page_title=cfg.settings.dashboard.title, page_icon="🌱", layout="wide")
if migrate.current_revision(cfg.db_path) != migrate.head_revision():
    try:
        migrate.upgrade(cfg.db_path)          # first start (or after an update): create / upgrade the database
    except Exception as exc:  # noqa: BLE001
        st.error(f"The database could not be prepared: {exc}. In PowerShell run: `vjh init`, then reload.")
        st.stop()
engine = _engine(str(cfg.db_path))


def session() -> Session:
    return Session(engine, expire_on_commit=False)


def open_job(job_id: int) -> None:
    st.session_state["job_id"] = int(job_id)
    st.session_state["page"] = "Job details"


def go(page_name: str) -> None:
    st.session_state["page"] = page_name


def page_header(title: str, subtitle: str = "") -> None:
    """Same header on every page: title plus one quiet line saying what the page is for."""
    st.header(f"{PAGE_ICON.get(title, '')} {title}".strip(), anchor=False)
    if subtitle:
        st.caption(subtitle)


def score_badge(score: float | None, category: str | None) -> str:
    """Markdown badge, e.g. ':green-badge[82 · Strong]'."""
    if score is None:
        return ":gray-badge[not evaluated]"
    color = CATEGORY_COLOR.get(category or "", "gray")
    return f":{color}-badge[{score:.0f} · {CATEGORY_LABEL.get(category or '', category or '')}]"


# ---------------- sidebar ----------------
st.sidebar.title(f"🌱 {cfg.settings.dashboard.title}")
IN_SETUP = setup.needs_setup(cfg) or bool(st.session_state.get("setup_mode"))
if IN_SETUP:                                  # the wizard: show its steps instead of the (empty) pages
    page = None
    _step = st.session_state.get("setup_step", 0)
    st.sidebar.markdown("\n".join(f"{'**' if i == _step else ''}{'✓' if i < _step else i + 1}. {name}"
                                  f"{'**' if i == _step else ''}  " for i, name in enumerate(setup.STEPS)))
else:
    page = st.sidebar.radio("Page", PAGES, key="page", label_visibility="collapsed",
                            format_func=lambda p: f"{PAGE_ICON.get(p, '')} {p}")
st.sidebar.divider()
with session() as _s:
    _last = data.runs_frame(_s, limit=1)
if len(_last):
    _r = _last.iloc[0]
    _ok = _r["status"] in ("ok", "success", "completed")
    st.sidebar.caption(f"{':material/check_circle:' if _ok else ':material/warning:'} Last run "
                       f"{pd.Timestamp(_r['started']):%d %b %H:%M} · {_r['status']}")
st.sidebar.caption(":material/lock: Local only (127.0.0.1). Nothing is sent to employers.")
st.sidebar.button("Reload data", icon=":material/refresh:", on_click=st.cache_data.clear)


# ---------------- pages ----------------
def page_overview() -> None:
    now = datetime.now()
    hello = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    page_header("Overview", f"{hello} · {now:%A %d %B %Y}")
    with session() as s:
        ov = data.overview(s, now, cfg.settings)
        steps = data.next_steps(s, now.date(), cfg.settings.thresholds.recommend_min_score,
                                cfg.settings.progress.weekly_application_goal, now)
    js, ap = ov["job_status"], ov["applications"]
    c = st.columns(5)
    c[0].metric("New today", ov["new_today"], border=True)
    c[1].metric("Waiting for AI", js.get("relevant", 0), border=True)
    c[2].metric("Evaluated", js.get("evaluated", 0), border=True)
    c[3].metric("In progress", sum(ap.get(k, 0) for k in ("recommended", "under_review",
                                                          "documents_generated", "approved")), border=True,
                help="Recommended, under review, documents generated or approved - not yet applied")
    c[4].metric("Applied / interview", ap.get("applied", 0) + ap.get("interview", 0), border=True)
    goal = cfg.settings.progress.weekly_application_goal
    if goal:
        with session() as s:
            done = data.progress(s, date.today(), cfg.settings.thresholds.recommend_min_score, goal,
                                 weeks=1)["this_week_applied"]
        st.progress(min(done / goal, 1.0), text=f"🎯 This week: {done} of {goal} applications")

    with st.container(border=True):
        st.markdown("**:material/checklist: Next steps**")
        if not steps:
            st.caption("Nothing to do right now. New jobs arrive with the daily run.")
        for i, step in enumerate(steps[:6]):
            a, b = st.columns([5, 1], vertical_alignment="center")
            a.markdown(f"{step.icon} " + (f":red[**{step.text}**]" if step.urgent else step.text))
            b.button("Open", key=f"step{i}", on_click=go, args=(step.page,), width="stretch")

    if ov["backend"] == "claude_code":
        st.caption(f"AI usage today: {ov['evaluations_today']} of {ov['subscription_limit']} evaluations "
                   "(Claude subscription, no extra bill).")
    else:
        spent = sum(u["cost_usd"] for u in ov["usage_today"])
        st.caption(f"API spend today: ${spent:.2f} of ${ov['cost_cap']:.2f}.")

    left, right = st.columns(2)
    with left:
        st.subheader("Due in the next 7 days")
        if ov["due"]:
            today = date.today()
            st.dataframe(pd.DataFrame([{"date": d.when, "what": d.kind.replace("_", " "),
                                        "overdue": "⚠️" if d.when < today else "", "job": d.job_id,
                                        "title": d.title, "employer": d.employer} for d in ov["due"]]),
                         hide_index=True)
        else:
            st.success("Nothing due.")
    with right:
        st.subheader("Sources needing attention")
        prob = ov["source_problems"]
        if len(prob):
            st.dataframe(prob[["source", "state", "last_success", "error"]], hide_index=True)
        else:
            st.success("All automatic sources OK.")
        if ov["last_run"]:
            r = ov["last_run"]
            st.caption(f"Last run #{r.id}: {r.started_at:%Y-%m-%d %H:%M}, status {r.status}.")

    st.subheader("Top recommendations")
    with session() as s:
        df = data.jobs_frame(s)
    top = df[df["score"].notna() & ~df["application"].isin(["archived", "rejected"])].head(8)
    _job_table(top, key="ov_top")
    st.caption("Daily routine (PowerShell): `vjh run-daily` collects, filters and evaluates new jobs.")


def _job_table(df: pd.DataFrame, key: str) -> None:
    if df.empty:
        st.info("No jobs match.")
        return
    view = df[["id", "score", "category", "recommendation", "ineligible", "title", "employer", "location",
               "application", "salary", "deadline", "source"]].copy()
    view["category"] = view["category"].map(lambda c: CATEGORY_LABEL.get(c, c))
    view["ineligible"] = view["ineligible"].map(lambda b: "⚠️ mandatory not met" if b else "")
    view["title"] = [data.title_link(u, t, e) for u, t, e in zip(df["url"], df["title"], df["employer"], strict=True)]
    event = st.dataframe(view, hide_index=True, on_select="rerun", selection_mode="multi-row", key=f"{key}_tbl",
                         column_config={"score": st.column_config.ProgressColumn("score", min_value=0,
                                                                                 max_value=100, format="%.0f"),
                                        "ineligible": st.column_config.TextColumn("eligibility"),
                                        "title": TITLE_LINK})
    picked = [int(view.iloc[i]["id"]) for i in event.selection.rows] if event else []
    _quick_actions(picked, key)


TITLE_LINK = st.column_config.LinkColumn(
    "title", display_text=data.TITLE_LINK_DISPLAY,
    help="Click a title to open the job ad (where you apply). 🔎 = no link stored: a web search instead.")


def _quick_actions(picked: list[int], key: str) -> None:
    """Action bar for the rows ticked in a table: open, rate, mark applied, change status."""
    if not picked:
        st.caption("☝️ Tick one or more rows (left edge of the table) to open, rate or update them.")
        return
    from vjh.dashboard import actions
    from vjh.tracker.service import TrackerError
    st.markdown(f"**Selected:** {', '.join(f'#{i}' for i in picked)}")
    if len(picked) == 1:
        st.button("📄 Open this job", key=f"{key}_open", on_click=open_job, args=(picked[0],), type="primary")
    rate_tab, applied_tab, status_tab = st.tabs(["👍 / 👎 Rate", "✉️ I applied", "🔀 Change status"])
    with rate_tab, st.form(f"{key}_rate"):
        good = st.radio("Was this a good recommendation?", ["👍 Good match", "👎 Not for me"], horizontal=True)
        is_good = good.startswith("👍")
        choice = st.selectbox("Main reason", actions.GOOD_REASONS + actions.BAD_REASONS + ["Other"],
                              help="Your ratings feed the Feedback page, which suggests better search settings.")
        extra = st.text_input("Anything to add? (optional)")
        archive = st.checkbox("Archive it (only for 👎)", value=True)
        if st.form_submit_button("Save rating"):
            reason = actions.reason_text("" if choice == "Other" else choice, extra)
            try:
                msgs = actions.rate(engine, picked, is_good, reason, archive=archive and not is_good)
                st.success("Saved: " + "; ".join(msgs))
            except TrackerError as exc:
                st.error(str(exc))
    with applied_tab, st.form(f"{key}_applied"):
        st.caption("Only after you have really sent the application yourself. A follow-up reminder is set.")
        when = st.date_input("Date you sent it", value=date.today())
        if st.form_submit_button("Mark as applied"):
            _do_status(actions, picked, "applied", when)
    with status_tab, st.form(f"{key}_status"):
        new = st.selectbox("New status", APP_STATUSES, index=APP_STATUSES.index("under_review"))
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Save status"):
            _do_status(actions, picked, new, date.today(), note)


def _do_status(actions, picked: list[int], status: str, when: date, note: str = "") -> None:
    from vjh.tracker.service import TrackerError
    try:
        msgs, warns = actions.change_status(engine, picked, status, when, note)
    except TrackerError as exc:
        st.error(str(exc))
        return
    st.success("Saved: " + "; ".join(msgs))
    for w in warns:
        st.warning(w)


def page_jobs() -> None:
    page_header("Jobs", "Every job found, best first. Tick rows to rate, mark applied or change status; click a title to open the ad.")
    with session() as s:
        df = data.jobs_frame(s)
    with st.expander("Filters", expanded=True):
        a, b, c = st.columns(3)
        smin, smax = a.slider("Score", 0, 100, (0, 100))
        unscored = a.checkbox("Include jobs without a score", value=False)
        text = b.text_input("Title or employer contains")
        employer = b.text_input("Employer")
        location = c.text_input("Location contains (e.g. Wien, Tulln)")
        cats = c.multiselect("Category", list(CATEGORY_LABEL), format_func=CATEGORY_LABEL.get)
        d, e, f = st.columns(3)
        langs = d.multiselect("Ad language", ["de", "en", "unknown"])
        job_statuses = d.multiselect("Pipeline status", sorted(df["job_status"].dropna().unique()),
                                     default=["evaluated"] if "evaluated" in set(df["job_status"]) else [])
        apps = e.multiselect("Application status", APP_STATUSES)
        min_salary = e.number_input("Minimum advertised annual salary (EUR, only jobs that state one)",
                                    min_value=0, value=0, step=1000)
        since = f.date_input("Published since", value=None)
    filt = data.JobFilters(min_score=smin, max_score=smax, include_unscored=unscored, text=text,
                           employer=employer, location=location, categories=tuple(cats), languages=tuple(langs),
                           job_statuses=tuple(job_statuses), applications=tuple(apps),
                           min_salary=float(min_salary) if min_salary else None, published_since=since)
    out = data.filter_jobs(df, filt)
    st.caption(f"{len(out)} of {len(df)} jobs")
    _job_table(out, key="jobs")


def page_job_details() -> None:
    page_header("Job details")
    job_id = st.session_state.get("job_id")
    if job_id is None:
        st.info("Open a job from the Jobs or Overview page.")
        return
    with session() as s:
        d = data.job_detail(s, int(job_id))
    if d is None:
        st.error(f"No job #{job_id}.")
        return
    j, ev, ai, app = d["job"], d["evaluation"], d["ai"], d["application"]
    st.subheader(f"#{j.id} {j.title}")
    st.markdown(f"**{j.employer}** · {j.location or 'location not stated'} · source: {j.source} · "
                + data.md_link("open the ad ↗" if data.is_web_url(j.url) else "search for the ad 🔎",
                               j.url, j.employer, search_title=j.title))
    st.markdown(" ".join(b for b in [
        score_badge(ev.overall_score if ev else None, ev.category if ev else None),
        f":violet-badge[AI: {ev.recommendation}]" if ev else "",
        f":gray-badge[:material/flag: {(app.status if app else 'not tracked').replace('_', ' ')}]",
        ":red-badge[:material/warning: mandatory not met]" if ev and ev.potentially_ineligible else "",
        f"[:blue-badge[:material/description: {len(d['documents'])} document(s) ready]](#documents)"
        if d["documents"] else ""] if b))
    c = st.columns([1, 3])
    c[0].metric("Score", f"{ev.overall_score:.0f}" if ev else "-", border=True)
    summary = (ev.explanation or "") if ev else "Not evaluated yet."
    c[1].markdown(summary if len(summary) <= 420 else summary[:420].rsplit(" ", 1)[0] + " …")
    info = st.columns(4)
    info[0].write(f"**Salary (as advertised):** {d['salary'] or 'not stated'}")
    info[1].write("**Commute:** unavailable (no routing service chosen)")
    info[2].write(f"**Deadline:** {j.deadline or 'not stated'}")
    info[3].write(f"**Ad language:** {d['language']}")
    if j.status != "evaluated" and j.filter_reason:
        st.info(f"Stage 1: {j.status} - {j.filter_reason}")
    _red_flags(j)

    if ev and ai:
        if ev.potentially_ineligible:
            st.warning("Potentially ineligible: at least one mandatory requirement is not met. Check below.")
        elif ev.eligibility_result == "unclear":
            st.info("Eligibility unclear: some mandatory requirements are unknown or only partly met.")
        t1, t2, t3, t4 = st.tabs(["Eligibility", "Score breakdown", "Skills", "Explanation"])
        with t1:
            reqs = pd.DataFrame([{"type": r["kind"], "status": REQ_LABEL[r["status"]], "requirement": r["text"],
                                  "quote from the ad": r.get("quote", ""), "evidence": ", ".join(r.get("evidence_ids", [])),
                                  "reason": r.get("reason", "")} for r in ai["requirements"]])
            st.dataframe(reqs, hide_index=True)
        with t2:
            pts = ai.get("_python", {}).get("points", {})
            for sub in ai["sub_scores"]:
                dim = sub["dimension"]
                w = cfg.settings.scoring.weights[dim]
                st.progress(min(pts.get(dim, 0) / w, 1.0), text=f"{dim.replace('_', ' ')}: {pts.get(dim, 0):.1f} / {w:g}")
                st.caption(sub["reason"])
            st.caption(f"Professional suitability {ev.professional_suitability:.0f}% · career attractiveness "
                       f"{ev.career_attractiveness:.0f}% · CV profile {ev.cv_profile} · model {ev.model}")
        with t3:
            a, b = st.columns(2)
            a.write("**Matching**")
            a.write("\n".join(f"- {x}" for x in ai.get("best_matching_experience", []) + ai.get("technical_skill_matches", []))
                    or "-")
            b.write("**Missing**")
            b.write("\n".join(f"- {x}" for x in ai.get("missing_skills", [])) or "-")
            if ai.get("language_concerns"):
                st.write("**Language concerns:** " + "; ".join(ai["language_concerns"]))
            if ai.get("potential_obstacles"):
                st.write("**Obstacles:** " + "; ".join(ai["potential_obstacles"]))
        with t4:
            st.write(ev.explanation)
            if ai.get("career_development"):
                st.write(f"**Career development:** {ai['career_development']}")
            for n in ai.get("_python", {}).get("notes", []):
                st.caption(f"note: {n}")
    else:
        st.info("Not evaluated yet. Evaluate it with: `vjh evaluate --job " + str(j.id) + "`")

    with st.expander("Job advertisement text"):
        st.text(j.description or "(no text)")

    st.subheader("Documents", anchor="documents")
    for kind, msg in st.session_state.pop(f"genmsg{j.id}", []):
        getattr(st, kind)(msg)
    if d["documents"]:
        folder = Path(d["documents"][-1].file_path).parent
        top = st.columns([3, 1])
        top[0].caption(f"{len(d['documents'])} document(s) in `{folder}`, newest first.")
        if folder.exists() and top[1].button("Open folder", key=f"fold{j.id}", icon=":material/folder_open:"):
            _open_file(folder)
        _keyword_match(d["documents"], ai)
        _documents_list(d["documents"], key=f"jd{j.id}")
    else:
        st.caption("No documents yet. Create them below; they appear here as soon as they are ready.")
    with st.form(f"gen{j.id}"):
        g1, g2 = st.columns(2)
        langs = g1.multiselect("Languages", ["en", "de", "it"], default=["en"])
        kinds = g2.multiselect("Documents", ["cv", "letter"], default=["cv", "letter"])
        create = st.form_submit_button("Create documents (about 1 minute each, uses your Claude subscription)")
    if create and langs and kinds:
        _generate(j.id, langs, kinds)

    st.subheader("Application")
    _status_form(j.id, app)
    if d["history"]:
        with st.expander("History"):
            st.dataframe(pd.DataFrame([{"when": e.occurred_at, "from": e.from_status, "to": e.to_status,
                                        "note": e.note} for e in d["history"]]), hide_index=True)
    _coach_section(j, ai, app, d["documents"])
    st.subheader("Feedback on this recommendation")
    for fb in d["feedback"]:
        st.caption(f"{'👍' if fb.accepted else '👎'} {fb.created_at:%Y-%m-%d}: {fb.reason}")
    _feedback_form(j.id)


def _red_flags(j) -> None:
    """Free warning signs on the ad (old, reposted, no pay, agency...). Hints, not proof."""
    from vjh.scoring.red_flags import job_flags
    with session() as s:
        flags = job_flags(s, j)
    if not flags:
        return
    warn = [f for f in flags if f.level == "warn"]
    with st.expander(f":material/report: {len(flags)} thing(s) to check about this ad"
                     + (" (worth a look)" if warn else ""), expanded=bool(warn)):
        for f in flags:
            st.markdown(f"{':orange[:material/warning:]' if f.level == 'warn' else ':gray[:material/info:]'} {f.text}")
        st.caption("Signals, not proof: many old or reposted ads are real. They help you decide where to spend time.")


def _coach_folder(j, docs) -> Path:
    from vjh.documents.generate import slug
    if docs:
        return Path(docs[-1].file_path).parent
    return cfg.data_dir / "documents" / f"{j.id}-{slug(j.employer)}"


def _coach_llm(job_id: int):
    from vjh.cli_pipeline import _llm, backend_ready
    from vjh.documents.generate import _Recorder
    ok, why = backend_ready(cfg)
    if not ok:
        st.error(why)
        return None
    rec = _Recorder(_llm(cfg), engine, cfg.settings, job_id)
    rec.purpose = "coach"
    return rec


def _coach_section(j, ai: dict | None, app, docs) -> None:
    """Interview preparation pack and follow-up / thank-you email drafts (never sent)."""
    from vjh.documents.coach import EMAIL_KINDS, PREP_LABEL
    st.subheader("Interview and follow-up", anchor="coach")
    status = app.status if app else "new"
    profile = load_profile(cfg.profile_path)
    folder = _coach_folder(j, docs)
    t1, t2 = st.tabs([":material/record_voice_over: Interview preparation", ":material/mail: Email drafts"])
    with t1:
        if status not in ("applied", "interview", "offer"):
            st.caption("Useful once you have applied or have an interview, but you can create it any time.")
        for kind, msg in st.session_state.pop(f"prepmsg{j.id}", []):
            getattr(st, kind)(msg)
        preps = [p for lbl in PREP_LABEL.values() for p in sorted(folder.glob(f"*_{lbl}_*.docx"))]
        for p in preps:
            row = st.columns([4, 1])
            row[0].markdown(f":material/article: `{p.name}`")
            if row[1].button("Open", key=f"prep{j.id}{p.name}", icon=":material/open_in_new:"):
                _open_file(p)
        st.caption("Likely questions with answer outlines built only from your real experience, honest answers "
                   "for the gaps, questions to ask and a checklist. Private notes for you, saved as Word.")
        with st.form(f"prepform{j.id}"):
            lang = st.selectbox("Language", ["en", "de", "it"], key=f"preplang{j.id}")
            go_prep = st.form_submit_button("Create interview preparation (about 1-2 minutes, uses your Claude plan)")
        if go_prep:
            _make_prep(j, ai, profile, folder, lang)
    with t2:
        st.caption("A short draft to copy into your own email. Nothing is sent by this program.")
        with st.form(f"mailform{j.id}"):
            a, b = st.columns(2)
            default = 1 if status == "interview" else 0
            kind = a.selectbox("Email", list(EMAIL_KINDS), index=default, key=f"mailkind{j.id}",
                               format_func=lambda k: {"follow_up": "Follow-up (no answer yet)",
                                                      "thank_you": "Thank-you after the interview"}[k])
            lang = b.selectbox("Language", ["en", "de", "it"], key=f"maillang{j.id}")
            context = st.text_input("Anything to mention (optional), e.g. who you spoke to or a topic from the "
                                    "interview", key=f"mailctx{j.id}")
            go_mail = st.form_submit_button("Write draft")
        if go_mail:
            llm = _coach_llm(j.id)
            if llm:
                from vjh.documents.coach import write_email
                try:
                    with st.spinner("Writing the draft..."):
                        st.session_state[f"mail{j.id}"] = write_email(
                            llm, kind=kind, job=j, evaluation=ai, profile=profile, settings=cfg.settings,
                            lang=lang, context=context)
                except Exception as exc:  # noqa: BLE001 - show the reason, keep the page working
                    st.error(f"Could not write the draft: {exc}")
        draft = st.session_state.get(f"mail{j.id}")
        if draft:
            st.markdown("**Subject** (hover to copy)")
            st.code(draft.subject, language=None)
            st.markdown("**Text**")
            st.code(draft.body, language=None, wrap_lines=True)
            st.caption("Read it, change what you like, then send it yourself.")


def _make_prep(j, ai: dict | None, profile, folder: Path, lang: str) -> None:
    from vjh.documents.coach import prep_filename, render_prep, write_interview_prep
    llm = _coach_llm(j.id)
    if llm is None:
        return
    try:
        with st.spinner("Preparing your interview pack..."):
            prep, notes = write_interview_prep(llm, job=j, evaluation=ai, profile=profile, settings=cfg.settings,
                                               lang=lang)
            path = render_prep(prep, profile, j, folder / prep_filename(profile.personal.full_name, j.title, lang))
    except Exception as exc:  # noqa: BLE001
        st.session_state[f"prepmsg{j.id}"] = [("error", f"Could not create the preparation: {exc}")]
    else:
        msgs = [("success", f"Ready: `{path.name}` ({len(prep.likely_questions)} questions, "
                            f"{len(prep.gap_questions)} gaps)")]
        msgs += [("caption", n) for n in notes]
        st.session_state[f"prepmsg{j.id}"] = msgs
    st.rerun()


def _keyword_match(docs, ai: dict | None) -> None:
    """ATS keyword check for the newest CV: which of the ad's skills that the candidate truly has
    are actually written in the CV (local and free, in the spirit of Resume Matcher / Jobscan)."""
    from vjh.documents.qa import keyword_coverage
    from vjh.documents.render import docx_text
    keywords = (ai or {}).get("technical_skill_matches") or []
    cvs = [x for x in docs if x.doc_type == "cv" and Path(x.file_path).exists()]
    if not keywords or not cvs:
        return
    newest = max(cvs, key=lambda x: (x.created_at, x.id))
    try:
        text = docx_text(Path(newest.file_path))
    except Exception:  # noqa: BLE001 - a damaged or locked file must not break the page
        st.caption(f"ATS keyword check skipped: could not read {Path(newest.file_path).name}.")
        return
    present, missing = keyword_coverage(text, keywords)
    share = len(present) / (len(present) + len(missing))
    with st.container(border=True):
        st.markdown(f"**:material/manage_search: ATS keyword match** · CV v{newest.version} ({newest.language}): "
                    f"**{len(present)} of {len(present) + len(missing)}** matching skills from the ad are in the CV")
        st.progress(share)
        st.markdown(" ".join([f":green-badge[{k}]" for k in present] + [f":gray-badge[missing: {k}]" for k in missing]))
        if missing:
            st.caption("Applicant tracking systems rank CVs by such keywords. You have these skills, so a new "
                       "version could mention them.")


def _generate(job_id: int, langs: list[str], kinds: list[str]) -> None:
    from vjh.cli_docs import _photo
    from vjh.cli_pipeline import _llm, backend_ready
    from vjh.documents.generate import generate_for_job
    from vjh.tracker.service import sync_documents
    ok, why = backend_ready(cfg)
    if not ok:
        st.error(why)
        return
    profile = load_profile(cfg.profile_path)
    home = load_home_location(cfg.home_location_path) if cfg.home_location_path.exists() else None
    with st.spinner("Writing and checking documents..."):
        results = generate_for_job(engine, cfg.settings, profile, home, _llm(cfg), job_id=job_id, langs=langs,
                                   kinds=kinds, out_root=cfg.data_dir / "documents", photo=_photo(cfg, profile))
        with Session(engine) as s, s.begin():
            sync_documents(s, job_id)
    msgs = []
    for r in results:
        name = f"{DOC_NAME.get(r.kind, r.kind)} ({r.lang})"
        if r.error:
            msgs.append(("error", f"{name}: {r.error}"))
        elif r.qa.passed:
            msgs.append(("success", f"{name} is ready: `{r.docx.name}` (QA passed)"))
        else:
            msgs.append(("warning", f"{name} is ready: `{r.docx.name}`, but QA FAILED - read the report"))
    # show the new files in the list straight away (the list is drawn above the form)
    st.session_state[f"genmsg{job_id}"] = msgs
    st.rerun()


def _status_form(job_id: int, app) -> None:
    with st.form(f"status{job_id}"):
        a, b, c = st.columns(3)
        current = app.status if app else "new"
        new = a.selectbox("Set status", APP_STATUSES, index=APP_STATUSES.index(current))
        when = a.date_input("Date", value=date.today())
        interview = b.text_input("Interview date and time (for 'interview'), e.g. 2026-11-25 10:00")
        follow = b.date_input("Follow-up date (optional)", value=None)
        response = c.text_input("Employer response (optional)")
        note = c.text_input("Note (optional)")
        submit = st.form_submit_button("Save status")
    if submit:
        from vjh.tracker.service import TrackerError, set_status
        try:
            with Session(engine) as s, s.begin():
                ch = set_status(s, job_id, new, note=note, when=when,
                                interview_at=datetime.fromisoformat(interview) if interview else None,
                                follow_up=follow, response=response or None)
                follow_date = ch.app.follow_up_date
        except (TrackerError, ValueError) as exc:
            st.error(str(exc))
            return
        st.success(f"Status: {ch.from_status} → {ch.to_status}"
                   + (f". Follow-up reminder: {follow_date}" if new == "applied" else ""))
        for w in ch.warnings:
            st.warning(w)


def _feedback_form(job_id: int) -> None:
    with st.form(f"fb{job_id}"):
        good = st.radio("Was this a good recommendation for you?", ["Good", "Not for me"], horizontal=True)
        reason = st.text_input("Why? (required)")
        archive = st.checkbox("Also archive this job (only for 'Not for me')")
        submit = st.form_submit_button("Save feedback")
    if submit:
        from vjh.tracker.feedback import add_feedback, dismiss
        from vjh.tracker.service import TrackerError
        try:
            with Session(engine) as s, s.begin():
                if good == "Not for me" and archive:
                    dismiss(s, job_id, reason)
                else:
                    add_feedback(s, job_id, good == "Good", reason)
        except TrackerError as exc:
            st.error(str(exc))
            return
        st.success("Feedback saved.")


DOC_NAME = {"cv": "CV", "cover_letter": "Cover letter"}
DOC_STATUS = {"qa_passed": ("✅", "QA passed - read it, then approve"), "qa_failed": ("❌", "QA failed - read the report"),
              "approved": ("👍", "approved by you"), "rejected": ("🗑️", "rejected"), "draft": ("•", "draft")}


def _open_file(path: Path) -> None:
    """The dashboard runs on this PC, so a file can open straight in Word / the PDF viewer / Explorer."""
    import os
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
    except (AttributeError, OSError) as exc:
        st.error(f"Could not open {path.name}: {exc}. Use the download button instead.")


def _documents_list(docs: list[GeneratedDocumentRow], key: str, show_job: bool = False) -> None:
    """Newest first (CV before its letter). Each document: what it is, its file name, open / download,
    approve / reject, QA report."""
    for doc in sorted(docs, key=lambda d: (d.created_at.date(), d.version, d.doc_type == "cv", d.id), reverse=True):
        path = Path(doc.file_path)
        pdf = path.with_suffix(".pdf")
        icon, status = DOC_STATUS.get(doc.approval_status, ("•", doc.approval_status))
        with st.container(border=True):
            st.markdown(f"{icon} **{DOC_NAME.get(doc.doc_type, doc.doc_type)} ({doc.language.upper()}) "
                        f"v{doc.version}** · {status}")
            st.caption(f"`{path.name}` · created {doc.created_at:%d.%m.%Y %H:%M} · document #{doc.id}")
            if not path.exists() and not pdf.exists():
                st.warning(f"The file is no longer in {path.parent} (moved or deleted).")
            row = st.container(horizontal=True, gap="small")     # buttons sized to their labels, in one row
            if pdf.exists() and row.button("Open PDF", key=f"{key}op{doc.id}", icon=":material/picture_as_pdf:",
                                           type="primary"):
                _open_file(pdf)
            if path.exists() and row.button("Open Word", key=f"{key}ow{doc.id}", icon=":material/description:"):
                _open_file(path)
            if pdf.exists():
                row.download_button("PDF", pdf.read_bytes(), file_name=pdf.name, key=f"{key}p{doc.id}",
                                    icon=":material/download:")
            if path.exists():
                row.download_button("Word", path.read_bytes(), file_name=path.name, key=f"{key}w{doc.id}",
                                    icon=":material/download:")
            override = doc.approval_status == ApprovalStatus.QA_FAILED.value
            if row.button("Approve", key=f"{key}a{doc.id}", disabled=doc.approval_status == "approved",
                          icon=":material/thumb_up:"):
                if override and not st.session_state.get(f"{key}o{doc.id}"):
                    st.error("QA failed for this document: tick 'approve anyway' after reading the report.")
                else:
                    _set_doc(doc.id, True)
            if row.button("Reject", key=f"{key}r{doc.id}", disabled=doc.approval_status == "rejected",
                          icon=":material/thumb_down:"):
                _set_doc(doc.id, False)
            if show_job:
                row.button(f"Job #{doc.job_id}", key=f"{key}j{doc.id}", icon=":material/work:",
                           on_click=open_job, args=(doc.job_id,))
            if override:
                st.checkbox("approve anyway (I have read the QA report)", key=f"{key}o{doc.id}")
            qa_file = path.with_name(path.stem + "_qa.json")
            if qa_file.exists():
                import json
                rep = json.loads(qa_file.read_text(encoding="utf-8"))
                with st.expander(f"QA report: {'passed' if rep['passed'] else 'FAILED'}, "
                                 f"{len(rep['issues'])} note(s)"):
                    if rep.get("ai_summary"):
                        st.write(rep["ai_summary"])
                    st.dataframe(pd.DataFrame(rep["issues"]), hide_index=True)


def _set_doc(doc_id: int, approve: bool) -> None:
    from vjh.tracker.service import sync_documents
    with Session(engine) as s, s.begin():
        d = s.get(GeneratedDocumentRow, doc_id)
        d.approval_status = (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED).value
        job_id = d.job_id
    with Session(engine) as s, s.begin():
        new = sync_documents(s, job_id)
    st.success(f"Document #{doc_id} {'approved' if approve else 'rejected'}. Nothing was sent."
               + (" The application is now 'approved' - mark it 'applied' after you send it." if new == "approved" else ""))
    st.rerun()


def page_applications() -> None:
    page_header("Applications", "Where each application stands. Board for the overview, Table for changes.")
    with session() as s:
        df = data.applications_frame(s)
    if df.empty:
        st.info("No applications yet. Evaluated jobs with a good score appear here automatically.")
        return
    show_closed = st.checkbox("Show archived and rejected", value=False)
    if not show_closed:
        df = df[~df["status"].isin(["archived", "rejected"])]
    order = {v: i for i, v in enumerate(APP_STATUSES)}
    df = df.sort_values(by="status", key=lambda c: c.map(order), ascending=False).reset_index(drop=True)
    view = st.radio("View", ["Board", "Table"], horizontal=True, key="apps_view")
    if view == "Board":
        _board(df)
        return
    table = df.drop(columns=["url"]).assign(
        title=[data.title_link(u, t, e) for u, t, e in zip(df["url"], df["title"], df["employer"], strict=True)])
    event = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="multi-row", key="apps_tbl",
                         column_config={"title": TITLE_LINK, "score": st.column_config.ProgressColumn("score", min_value=0,
                                                                                 max_value=100, format="%.0f")})
    _quick_actions([int(df.iloc[i]["job"]) for i in event.selection.rows] if event else [], "apps")


BOARD = [("🔎 To review", ("new", "recommended", "under_review")),
         ("📝 Documents ready", ("documents_generated", "approved")),
         ("✉️ Applied", ("applied",)), ("🗣️ Interview", ("interview",)), ("🎉 Offer", ("offer",))]
BOARD_MAX_CARDS = 12


def _board(df: pd.DataFrame) -> None:
    """Applications as cards, one column per stage. Change a stage in the Table view or on the job's page."""
    cols = st.columns(len(BOARD))
    for col, (title, statuses) in zip(cols, BOARD, strict=True):
        part = df[df["status"].isin(statuses)]
        col.markdown(f"**{title}** · {len(part)}")
        for r in part.head(BOARD_MAX_CARDS).itertuples():
            with col.container(border=True):
                st.markdown(f"**#{r.job}** {data.md_link(r.title[:55], r.url, r.employer)}")
                bits = [r.employer[:35]]
                if pd.notna(r.score):
                    bits.append(f"score {r.score:.0f}")
                if pd.notna(r.applied):
                    bits.append(f"sent {r.applied}")
                if pd.notna(r.follow_up) and r.status == "applied":
                    bits.append(f"follow up {r.follow_up}")
                if pd.notna(r.interview):
                    bits.append(f"interview {pd.Timestamp(r.interview):%d.%m %H:%M}")
                if pd.notna(r.deadline) and r.status not in ("applied", "interview", "offer"):
                    bits.append(f"deadline {r.deadline}")
                st.caption(" · ".join(bits))
                st.button("Open", key=f"board_{r.job}", on_click=open_job, args=(int(r.job),))
        if len(part) > BOARD_MAX_CARDS:
            col.caption(f"+ {len(part) - BOARD_MAX_CARDS} more (Table view)")
    st.caption("Tip: switch to **Table**, tick jobs and use the buttons to rate them or change their status.")


def page_progress() -> None:
    import altair as alt
    page_header("Progress", "Your weekly goal, response rates and the path from found to offer.")
    with session() as s:
        p = data.progress(s, date.today(), cfg.settings.thresholds.recommend_min_score,
                          cfg.settings.progress.weekly_application_goal)
    goal, done, r = p["weekly_goal"], p["this_week_applied"], p["rates"]
    c = st.columns(5)
    c[0].metric("Applied this week", f"{done} / {goal}" if goal else done, border=True)
    c[1].metric("Weeks in a row on goal", p["streak"] if goal else "–", border=True)
    c[2].metric("Applications sent", r["applied"], border=True)
    c[3].metric("Response rate", f"{r['response_rate']}%" if r["response_rate"] is not None else "–",
                border=True, help="Applications that got an answer (interview, offer, rejection or a reply you recorded)")
    c[4].metric("Interview rate", f"{r['interview_rate']}%" if r["interview_rate"] is not None else "–", border=True)
    if goal:
        st.progress(min(done / goal, 1.0), text=("🎯 Weekly goal reached!" if done >= goal
                                                 else f"{goal - done} more to reach this week's goal"))
        st.caption("Change the goal in `config/settings.yaml` → `progress: weekly_application_goal`.")

    left, right = st.columns(2)
    with left:
        st.subheader("From found to offer")
        f = pd.DataFrame({"step": list(p["funnel"]), "jobs": list(p["funnel"].values())})
        base = alt.Chart(f).encode(y=alt.Y("step:N", sort=data.PROGRESS_STEPS, title=None),
                                   x=alt.X("jobs:Q", title="jobs", scale=alt.Scale(type="symlog")))
        st.altair_chart(base.mark_bar(color="#2e7d32") + base.mark_text(align="left", dx=4).encode(text="jobs:Q"),
                        use_container_width=True)
        st.caption("Log scale, so the small steps at the end stay visible.")
    with right:
        st.subheader("Last 12 weeks")
        wk = p["weekly"].copy()
        wk["week"] = pd.to_datetime(wk["week"])
        wk["label"] = wk["week"].dt.strftime("%d %b")
        sent = wk.melt("label", value_vars=["applied", "interviews"], var_name="what", value_name="count")
        top = max(goal, int(sent["count"].max() or 0), 1) + 1
        bars = alt.Chart(sent).mark_bar().encode(
            x=alt.X("label:N", sort=list(wk["label"]), title="week starting", axis=alt.Axis(labelAngle=-45)),
            xOffset="what:N",
            y=alt.Y("count:Q", title="applications", scale=alt.Scale(domain=[0, top]), axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("what:N", title=None, scale=alt.Scale(range=["#2e7d32", "#f9a825"])))
        chart = bars
        if goal:
            chart = bars + alt.Chart(pd.DataFrame({"goal": [goal]})).mark_rule(
                strokeDash=[4, 4], color="#9e9e9e").encode(y="goal:Q")
        st.altair_chart(chart, use_container_width=True)
        if goal:
            st.caption("Dashed line: your weekly goal.")
        st.caption("New jobs found per week: " + " · ".join(f"{int(v)}" for v in wk["found"]))

    a, b, c2 = st.columns(3)
    a.metric("Waiting for an answer", r["waiting"])
    b.metric("Rejections", r["rejections"])
    c2.metric("Your ratings", f"👍 {p['feedback_good']} · 👎 {p['feedback_bad']}")
    st.button("Go to the application board", on_click=lambda: st.session_state.update(page="Applications"))


def page_documents() -> None:
    page_header("Documents", "Generated CVs and cover letters with their quality report. Approve before you send anything.")
    with session() as s:
        df = data.documents_frame(s)
        if df.empty:
            st.info("No documents yet. Create them from a job's page.")
            return
        st.dataframe(df.drop(columns=["file"]), hide_index=True)
        status = st.multiselect("Show", [s_.value for s_ in ApprovalStatus],
                                default=["qa_passed", "qa_failed", "draft"])
        docs = [s.get(GeneratedDocumentRow, int(i)) for i in df[df["status"].isin(status)]["doc"]]
    _documents_list([d for d in docs if d], key="docs", show_job=True)


def page_sources() -> None:
    page_header("Sources", "Where jobs come from and whether each source is working.")
    with session() as s:
        df = data.sources_frame(s, datetime.now())
    st.dataframe(df, hide_index=True)
    st.caption("Re-check robots.txt and feeds with `vjh sources check`. Sources that are not automatic are "
               "covered by job-alert emails (save them as .eml in data\\imports\\alerts) or manual import.")


def page_logs() -> None:
    page_header("Logs & errors", "Recent runs and anything that went wrong.")
    with session() as s:
        runs = data.runs_frame(s)
    st.subheader("Recent runs")
    st.dataframe(runs.drop(columns=["log"]), hide_index=True)
    errors = runs[runs["errors"] > 0]
    if len(errors):
        st.subheader("Errors")
        for r in errors.itertuples():
            st.error(f"Run {r.run} ({r.kind}, {r.started:%Y-%m-%d %H:%M}): {r.error_detail}")
    path, text = data.latest_log(cfg.data_dir)
    st.subheader("Latest log file")
    if path:
        st.caption(str(path))
        st.code(text or "(empty)", language=None)
    else:
        st.caption("No log files yet.")


def page_feedback() -> None:
    page_header("Feedback", "What your ratings say about the search, with suggestions (nothing changes automatically).")
    from vjh.tracker.feedback import build_report
    terms = load_yaml(cfg.root / "config" / "search_terms.yaml")
    with session() as s:
        rep = build_report(s, cfg.settings.thresholds.recommend_min_score,
                           terms.get("relevance", {}).get("exclude_title_patterns", []))
    st.markdown(rep.to_markdown())
    st.caption("Suggestions only - nothing is changed automatically. Give feedback from each job's page.")


def page_import() -> None:
    page_header("Import a job", "Add a job you found yourself: a web address, pasted text or a file.")
    st.caption("For jobs from sites that are not collected automatically. The ad text is stored as data only.")
    from vjh.sources.imports import ImportRefused, import_file, import_text, import_url
    tab_url, tab_text, tab_file = st.tabs(["From a web address", "Paste the text", "Upload a file"])
    cj = None
    with tab_url, st.form("imp_url"):
        url = st.text_input("Web address of the job ad")
        t1, e1 = st.columns(2)
        title = t1.text_input("Title (optional)", key="u_t")
        employer = e1.text_input("Employer (optional)", key="u_e")
        if st.form_submit_button("Import") and url:
            from vjh.sources.http import FetchError, PoliteClient, RobotsDisallowed
            try:
                with PoliteClient(cfg.settings.collection) as client:
                    cj = import_url(client, url, title=title or None, employer=employer or None)
            except (ImportRefused, RobotsDisallowed, FetchError) as exc:
                st.error(f"{exc}\n\nCopy the ad text and use 'Paste the text' instead.")
    with tab_text, st.form("imp_text"):
        t2, e2 = st.columns(2)
        title2 = t2.text_input("Title (required)", key="t_t")
        employer2 = e2.text_input("Employer (required)", key="t_e")
        url2 = st.text_input("Web address (optional)", key="t_u")
        body = st.text_area("Job ad text", height=250)
        if st.form_submit_button("Import") and body:
            try:
                cj = import_text(body, title=title2, employer=employer2, url=url2 or None)
            except ImportRefused as exc:
                st.error(str(exc))
    with tab_file, st.form("imp_file"):
        up = st.file_uploader("Job ad file", type=["txt", "md", "html", "htm", "pdf"])
        t3, e3 = st.columns(2)
        title3 = t3.text_input("Title (needed for .txt/.pdf)", key="f_t")
        employer3 = e3.text_input("Employer (needed for .txt/.pdf)", key="f_e")
        if st.form_submit_button("Import") and up is not None:
            folder = cfg.data_dir / "imports" / "uploads"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / Path(up.name).name
            path.write_bytes(up.getvalue())
            try:
                cj = import_file(path, title=title3 or None, employer=employer3 or None)
            except ImportRefused as exc:
                st.error(str(exc))
    if cj is not None:
        _store_import(cj)


def _store_import(cj) -> None:
    from vjh.pipeline.stage1 import Rules, run_stage1
    from vjh.sources.store import store_jobs
    with Session(engine) as s, s.begin():
        new, _ = store_jobs(s, [cj], run_id=None)
    if not new:
        st.warning(f"Already in the database: {cj.job.title} | {cj.job.employer}")
        return
    rules = Rules.from_settings(load_yaml(cfg.root / "config" / "search_terms.yaml"), cfg.settings)
    with Session(engine) as s, s.begin():
        run_stage1(s, rules)
    with session() as s:
        df = data.jobs_frame(s)
    row = df[(df["title"] == cj.job.title) & (df["employer"] == cj.job.employer)].head(1)
    st.success(f"Imported: {cj.job.title} | {cj.job.employer} ({len(cj.job.description)} characters of text).")
    if len(row):
        r = row.iloc[0]
        st.write(f"Stage 1: **{r.job_status}** - {r.filter_reason}")
        st.caption(f"Evaluate it with: `vjh evaluate --job {r.id}`")
        st.button("Open the job", on_click=open_job, args=(int(r.id),))


if IN_SETUP:
    setup.page_setup(cfg)                     # first start: the wizard instead of empty pages
else:
    {"Overview": page_overview, "Progress": page_progress, "Jobs": page_jobs, "Job details": page_job_details,
     "Applications": page_applications, "Documents": page_documents, "Sources": page_sources,
     "Logs & errors": page_logs, "Feedback": page_feedback, "Import a job": page_import,
     "Settings": lambda: setup.page_settings(cfg)}[page]()
