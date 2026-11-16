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
from vjh.db import migrate
from vjh.db.tables import GeneratedDocumentRow
from vjh.models.common import ApplicationStatus, ApprovalStatus

PAGES = ["Overview", "Progress", "Jobs", "Job details", "Applications", "Documents", "Sources", "Logs & errors",
         "Feedback", "Import a job"]
APP_STATUSES = [s.value for s in ApplicationStatus]
CATEGORY_LABEL = {"excellent": "Excellent", "strong": "Strong", "good_potential": "Good potential",
                  "possible": "Possible", "low_priority": "Low priority"}
REQ_LABEL = {"met": "✅ met", "partially_met": "🟡 partial", "not_met": "❌ not met", "unknown": "❔ unknown"}


@st.cache_resource
def _cfg():
    return load_config()


@st.cache_resource
def _engine(db_path: str):
    return migrate.make_engine(Path(db_path))


cfg = _cfg()
st.set_page_config(page_title=cfg.settings.dashboard.title, page_icon="🌱", layout="wide")
if migrate.current_revision(cfg.db_path) != migrate.head_revision():
    st.error("The database is missing or out of date. In PowerShell run: `vjh init`, then reload this page.")
    st.stop()
engine = _engine(str(cfg.db_path))


def session() -> Session:
    return Session(engine, expire_on_commit=False)


def open_job(job_id: int) -> None:
    st.session_state["job_id"] = int(job_id)
    st.session_state["page"] = "Job details"


# ---------------- sidebar ----------------
st.sidebar.title(f"🌱 {cfg.settings.dashboard.title}")
page = st.sidebar.radio("Page", PAGES, key="page")
st.sidebar.caption("Local only (127.0.0.1). Nothing is sent to employers.")
if st.sidebar.button("Reload data"):
    st.rerun()


# ---------------- pages ----------------
def page_overview() -> None:
    st.header("Overview")
    with session() as s:
        ov = data.overview(s, datetime.now(), cfg.settings)
    js, ap = ov["job_status"], ov["applications"]
    c = st.columns(5)
    c[0].metric("New today", ov["new_today"])
    c[1].metric("Waiting for AI", js.get("relevant", 0))
    c[2].metric("Evaluated", js.get("evaluated", 0))
    c[3].metric("In progress", sum(ap.get(k, 0) for k in ("recommended", "under_review",
                                                          "documents_generated", "approved")),
                help="Recommended, under review, documents generated or approved - not yet applied")
    c[4].metric("Applied / interview", ap.get("applied", 0) + ap.get("interview", 0))
    goal = cfg.settings.progress.weekly_application_goal
    if goal:
        with session() as s:
            done = data.progress(s, date.today(), cfg.settings.thresholds.recommend_min_score, goal,
                                 weeks=1)["this_week_applied"]
        st.progress(min(done / goal, 1.0), text=f"🎯 This week: {done} of {goal} applications")

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
    st.header("Jobs")
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
    st.header("Job details")
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
    c = st.columns([1, 3])
    c[0].metric("Score", f"{ev.overall_score:.0f}" if ev else "-")
    c[1].markdown(
        f"**Category:** {CATEGORY_LABEL.get(ev.category, '-') if ev else '-'}  \n"
        f"**AI recommendation:** {ev.recommendation.upper() if ev else '-'}  \n"
        f"**Application status:** {(app.status if app else 'none yet').replace('_', ' ')}")
    info = st.columns(4)
    info[0].write(f"**Salary (as advertised):** {d['salary'] or 'not stated'}")
    info[1].write("**Commute:** unavailable (no routing service chosen)")
    info[2].write(f"**Deadline:** {j.deadline or 'not stated'}")
    info[3].write(f"**Ad language:** {d['language']}")
    if j.status != "evaluated" and j.filter_reason:
        st.info(f"Stage 1: {j.status} - {j.filter_reason}")

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

    st.subheader("Documents")
    if d["documents"]:
        _documents_list(d["documents"], key=f"jd{j.id}")
    else:
        st.caption("No documents yet.")
    with st.form(f"gen{j.id}"):
        g1, g2 = st.columns(2)
        langs = g1.multiselect("Languages", ["en", "de", "it"], default=["en"])
        kinds = g2.multiselect("Documents", ["cv", "letter"], default=["cv", "letter"])
        go = st.form_submit_button("Create documents (about 1 minute each, uses your Claude subscription)")
    if go and langs and kinds:
        _generate(j.id, langs, kinds)

    st.subheader("Application")
    _status_form(j.id, app)
    if d["history"]:
        with st.expander("History"):
            st.dataframe(pd.DataFrame([{"when": e.occurred_at, "from": e.from_status, "to": e.to_status,
                                        "note": e.note} for e in d["history"]]), hide_index=True)
    st.subheader("Feedback on this recommendation")
    for fb in d["feedback"]:
        st.caption(f"{'👍' if fb.accepted else '👎'} {fb.created_at:%Y-%m-%d}: {fb.reason}")
    _feedback_form(j.id)


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
    for r in results:
        if r.error:
            st.error(f"{r.kind} ({r.lang}): {r.error}")
        elif r.qa.passed:
            st.success(f"{r.kind} ({r.lang}): document #{r.doc_id}, QA passed")
        else:
            st.warning(f"{r.kind} ({r.lang}): document #{r.doc_id}, QA FAILED - read the report")


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


def _documents_list(docs: list[GeneratedDocumentRow], key: str) -> None:
    for doc in docs:
        path = Path(doc.file_path)
        cols = st.columns([3, 1, 1, 1, 1])
        icon = {"qa_passed": "✅", "qa_failed": "❌", "approved": "👍", "rejected": "🗑️"}.get(doc.approval_status, "•")
        cols[0].write(f"{icon} #{doc.id} {doc.doc_type} ({doc.language}) v{doc.version} - {doc.approval_status}")
        if path.exists():
            cols[1].download_button("Word", path.read_bytes(), file_name=path.name, key=f"{key}w{doc.id}")
        pdf = path.with_suffix(".pdf")
        if pdf.exists():
            cols[2].download_button("PDF", pdf.read_bytes(), file_name=pdf.name, key=f"{key}p{doc.id}")
        override = doc.approval_status == ApprovalStatus.QA_FAILED.value
        if cols[3].button("Approve", key=f"{key}a{doc.id}", disabled=doc.approval_status == "approved"):
            if override and not st.session_state.get(f"{key}o{doc.id}"):
                st.error("QA failed for this document: tick 'approve anyway' after reading the report.")
            else:
                _set_doc(doc.id, True)
        if cols[4].button("Reject", key=f"{key}r{doc.id}", disabled=doc.approval_status == "rejected"):
            _set_doc(doc.id, False)
        if override:
            st.checkbox("approve anyway (I have read the QA report)", key=f"{key}o{doc.id}")
        qa_file = path.with_name(path.stem + "_qa.json")
        if qa_file.exists():
            import json
            rep = json.loads(qa_file.read_text(encoding="utf-8"))
            with st.expander(f"QA report #{doc.id}: {'passed' if rep['passed'] else 'FAILED'}, "
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
    st.header("Applications")
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
    st.header("Progress")
    with session() as s:
        p = data.progress(s, date.today(), cfg.settings.thresholds.recommend_min_score,
                          cfg.settings.progress.weekly_application_goal)
    goal, done, r = p["weekly_goal"], p["this_week_applied"], p["rates"]
    c = st.columns(5)
    c[0].metric("Applied this week", f"{done} / {goal}" if goal else done)
    c[1].metric("Weeks in a row on goal", p["streak"] if goal else "–")
    c[2].metric("Applications sent", r["applied"])
    c[3].metric("Response rate", f"{r['response_rate']}%" if r["response_rate"] is not None else "–",
                help="Applications that got an answer (interview, offer, rejection or a reply you recorded)")
    c[4].metric("Interview rate", f"{r['interview_rate']}%" if r["interview_rate"] is not None else "–")
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
    st.header("Documents")
    with session() as s:
        df = data.documents_frame(s)
        if df.empty:
            st.info("No documents yet. Create them from a job's page.")
            return
        st.dataframe(df.drop(columns=["file"]), hide_index=True)
        status = st.multiselect("Show", [s_.value for s_ in ApprovalStatus],
                                default=["qa_passed", "qa_failed", "draft"])
        docs = [s.get(GeneratedDocumentRow, int(i)) for i in df[df["status"].isin(status)]["doc"]]
    _documents_list([d for d in docs if d], key="docs")


def page_sources() -> None:
    st.header("Sources")
    with session() as s:
        df = data.sources_frame(s, datetime.now())
    st.dataframe(df, hide_index=True)
    st.caption("Re-check robots.txt and feeds with `vjh sources check`. Sources that are not automatic are "
               "covered by job-alert emails (save them as .eml in data\\imports\\alerts) or manual import.")


def page_logs() -> None:
    st.header("Logs & errors")
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
    st.header("Feedback")
    from vjh.tracker.feedback import build_report
    terms = load_yaml(cfg.root / "config" / "search_terms.yaml")
    with session() as s:
        rep = build_report(s, cfg.settings.thresholds.recommend_min_score,
                           terms.get("relevance", {}).get("exclude_title_patterns", []))
    st.markdown(rep.to_markdown())
    st.caption("Suggestions only - nothing is changed automatically. Give feedback from each job's page.")


def page_import() -> None:
    st.header("Import a job")
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


{"Overview": page_overview, "Progress": page_progress, "Jobs": page_jobs, "Job details": page_job_details,
 "Applications": page_applications, "Documents": page_documents, "Sources": page_sources,
 "Logs & errors": page_logs, "Feedback": page_feedback, "Import a job": page_import}[page]()
