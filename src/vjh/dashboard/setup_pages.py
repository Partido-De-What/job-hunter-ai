"""Dashboard: first-run setup wizard and the Settings page.

The wizard reads the CV with the user's own Claude, asks only what a CV cannot answer
(honest language levels, where to work, what kind of job), drafts the search, sets up the
email report and the schedule, and starts the first run. Everything stays editable on the
Settings page. Passwords are typed by the user into their own .env and never shown again.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from vjh.automation import schedule
from vjh.config import AppConfig, load_profile, load_yaml
from vjh.models.settings import Settings
from vjh.setup import cv_import, files
from vjh.setup.search_setup import SearchAnswers, apply_search, draft_search

STEPS = ["Claude and your CV", "Your profile", "Your search", "Email reports", "Schedule", "Finish"]
LEVEL_HELP = ("native, C2 (mastery), C1 (fluent, professional), B2 (good, independent), B1 (intermediate), "
              "A2 (basic), A1 (beginner). Be honest: CVs and letters never claim more than this.")
# Common providers: host and port are filled in from the sender's address.
PROVIDERS = {"gmail.com": ("smtp.gmail.com", 587), "googlemail.com": ("smtp.gmail.com", 587),
             "gmx.at": ("mail.gmx.net", 587), "gmx.de": ("mail.gmx.net", 587), "gmx.net": ("mail.gmx.net", 587),
             "gmx.com": ("mail.gmx.com", 587), "web.de": ("smtp.web.de", 587),
             "yahoo.com": ("smtp.mail.yahoo.com", 465), "icloud.com": ("smtp.mail.me.com", 587),
             "me.com": ("smtp.mail.me.com", 587), "outlook.com": ("smtp-mail.outlook.com", 587),
             "hotmail.com": ("smtp-mail.outlook.com", 587)}
APP_PASSWORD_HELP = {
    "smtp.gmail.com": "Gmail needs an **app password** (not your normal password): turn on 2-step verification, "
                      "then create one at https://myaccount.google.com/apppasswords and paste the 16 letters here.",
    "smtp.mail.yahoo.com": "Yahoo needs an app password: Account security → Generate app password.",
    "smtp.mail.me.com": "iCloud needs an app-specific password: https://account.apple.com → Sign-In and Security.",
    "smtp-mail.outlook.com": "Outlook.com may refuse programs that sign in with a password. If the test fails, "
                             "use a Gmail or GMX address as the sender.",
}
DATA_SUBDIRS = ["logs", "imports/alerts", "documents", "reports", "cache"]


# ---------------- shared helpers ----------------

def reload_config() -> None:
    """Settings changed on disk: drop the cached config so the next run reads the new files."""
    st.cache_resource.clear()
    st.cache_data.clear()


def _settings_path(cfg: AppConfig) -> Path:
    return cfg.root / "config" / "settings.yaml"


def save_settings(cfg: AppConfig, changes: dict) -> bool:
    try:
        files.update_yaml(_settings_path(cfg), changes, validate=Settings.model_validate)
    except Exception as exc:  # noqa: BLE001 - shown to the user, nothing written
        st.error(f"Not saved: {exc}")
        return False
    reload_config()
    return True


def _llm(cfg: AppConfig):
    from vjh.cli_pipeline import _llm as make
    return make(cfg)


def claude_status(cfg: AppConfig) -> tuple[bool, str]:
    from vjh.cli_pipeline import backend_ready
    return backend_ready(cfg)


def _new_console(args: list[str], cwd: Path) -> None:
    """Start a program in its own window (Windows), so the user can watch and close it."""
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen(args, cwd=str(cwd), creationflags=flags)  # noqa: S603 - fixed arguments


def _claude_login_box(cfg: AppConfig) -> bool:
    ok, why = claude_status(cfg)
    if ok:
        st.success(f"Claude is connected: {why}.", icon=":material/check_circle:")
        return True
    st.warning(f"Claude is not connected yet: {why}", icon=":material/link_off:")
    st.markdown("Job Hunter uses **your own Claude subscription** (Pro or Max) through Claude Code. "
                "Click below: a window opens and your browser asks you to sign in to Claude. "
                "Then come back here and click *Check again*.")
    c = st.columns([1, 1, 3])
    if c[0].button("Sign in to Claude", icon=":material/login:", type="primary"):
        from vjh.llm.claude_code import find_cli
        exe = find_cli(cfg.settings.llm)
        if exe:
            _new_console([exe, "auth", "login"], cfg.root)
            st.info("A sign-in window has opened. Finish the sign-in there, then click *Check again*.")
        else:
            st.error("Claude Code is not installed. Run Setup.bat again, or install it from "
                     "https://claude.com/claude-code, then reopen this page.")
    c[1].button("Check again", icon=":material/refresh:")
    return False


# ---------------- wizard ----------------

def open_setup(step: int = 0) -> None:
    """Button callback (runs before the page is drawn): show the wizard at a step."""
    st.session_state["setup_mode"] = True
    st.session_state["setup_step"] = step


def finish_setup() -> None:
    st.session_state["setup_mode"] = False
    st.session_state["setup_step"] = 0
    st.session_state["page"] = "Overview"
    reload_config()


def needs_setup(cfg: AppConfig) -> bool:
    return not cfg.profile_path.exists()


def page_setup(cfg: AppConfig) -> None:
    st.session_state["setup_mode"] = True         # stays in the wizard until 'Go to the dashboard'
    step = st.session_state.setdefault("setup_step", 0)
    st.header(":material/rocket_launch: Set up Job Hunter", anchor=False)
    st.caption("About 10 minutes. You can change everything later on the Settings page.")
    if not needs_setup(cfg):
        st.button("Leave the setup", icon=":material/close:", on_click=finish_setup)
    st.progress(step / (len(STEPS) - 1), text=f"Step {step + 1} of {len(STEPS)}: {STEPS[step]}")
    [_step_cv, _step_profile, _step_search, _step_email, _step_schedule, _step_finish][step](cfg)


def _nav(back: bool = True, next_label: str | None = "Next", next_ok: bool = True) -> None:
    c = st.columns([1, 1, 4])
    if back and c[0].button("Back", icon=":material/arrow_back:"):
        st.session_state["setup_step"] -= 1
        st.rerun()
    if next_label and c[1].button(next_label, icon=":material/arrow_forward:", type="primary", disabled=not next_ok):
        st.session_state["setup_step"] += 1
        st.rerun()


def _extract_path(cfg: AppConfig) -> Path:
    return cfg.personal_dir / "cv_extract.json"


def _step_cv(cfg: AppConfig) -> None:
    st.subheader("1 · Connect Claude and add your CV", anchor=False)
    connected = _claude_login_box(cfg)
    cv_dir = cfg.root / cfg.settings.paths.cv_dir
    a, b = st.columns(2)
    with a:
        up = st.file_uploader("Your CV (PDF or Word)", type=["pdf", "docx"])
        if up is not None:
            p = cv_import.save_upload(cv_dir, up.name, up.getvalue())
            st.caption(f"Saved as `{p.name}` in your private cv folder.")
    with b:
        ph = st.file_uploader("Your photo (optional, JPG or PNG)", type=["jpg", "jpeg", "png"])
        if ph is not None:
            p = cv_import.save_upload(cv_dir, ph.name, ph.getvalue())
            st.image(str(p), width=120)
    cv, photo = cv_import.find_cv(cv_dir), cv_import.find_photo(cv_dir)
    st.caption(f"CV: `{cv.name if cv else 'none yet'}` · photo: `{photo.name if photo else 'none'}` "
               "· these files stay on your computer and are never changed.")
    done = _extract_path(cfg).exists()
    if cv and st.button("Read my CV (about 1 minute)", icon=":material/auto_awesome:", type="primary",
                        disabled=not connected):
        try:
            with st.spinner("Claude is reading your CV. Nothing is invented: unclear points become questions."):
                extracted = cv_import.read_cv(_llm(cfg), cv_import.cv_text(cv), cfg.settings)
            cfg.personal_dir.mkdir(parents=True, exist_ok=True)
            _extract_path(cfg).write_text(json.dumps(extracted, ensure_ascii=False, indent=1), encoding="utf-8")
            done = True
            st.success(f"Read {len(extracted.get('experiences', []))} jobs, {len(extracted.get('education', []))} "
                       f"degrees and {len(extracted.get('languages', []))} languages. Check them in the next step.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read the CV: {exc}")
    _nav(back=False, next_ok=done)


def _step_profile(cfg: AppConfig) -> None:
    st.subheader("2 · Check your profile", anchor=False)
    st.caption("Everything below comes from your CV. Fix what is wrong; CVs and letters only ever use these facts.")
    ex = json.loads(_extract_path(cfg).read_text(encoding="utf-8"))
    p = ex.get("personal") or {}
    with st.container(border=True):
        st.markdown("**About you**")
        c = st.columns(3)
        name = c[0].text_input("Full name", p.get("full_name", ""))
        email = c[1].text_input("Email (shown on your CV)", p.get("email", ""))
        phone = c[2].text_input("Phone (shown on your CV)", p.get("phone", ""))
        c = st.columns(3)
        nat = c[0].text_input("Nationality (comma-separated)", ", ".join(p.get("nationalities") or []))
        dob = c[1].text_input("Date of birth (YYYY-MM-DD, optional)", p.get("date_of_birth", ""))
        rtw = c[2].selectbox("Allowed to work in the country you are searching in?", ["Yes", "No", "Not sure"])
        rtw_note = st.text_input("Shown on your CV, e.g. 'EU citizen - no work permit needed' (optional)")
    with st.container(border=True):
        st.markdown("**Languages: your honest level** (required)")
        st.caption(LEVEL_HELP)
        rows = [{"language": x.get("language", ""), "on your CV": x.get("level_on_cv", ""), "level": None,
                 "certificate": x.get("certificate", "")} for x in ex.get("languages") or []]
        langs = st.data_editor(pd.DataFrame(rows or [{"language": "", "on your CV": "", "level": None,
                                                      "certificate": ""}]),
                               num_rows="dynamic", hide_index=True, key="lang_editor",
                               column_config={"level": st.column_config.SelectboxColumn(
                                   "level", options=cv_import.LEVELS, required=True),
                                   "on your CV": st.column_config.TextColumn(disabled=True)})
    with st.container(border=True):
        st.markdown("**Jobs** (fix dates as YYYY-MM; leave *end* empty and tick *current* for a job you still have)")
        jobs = st.data_editor(pd.DataFrame([{k: x.get(k, "") for k in ("employer", "title", "location", "start", "end")}
                                            | {"current": bool(x.get("current"))} for x in ex.get("experiences") or []]),
                              hide_index=True, num_rows="dynamic", key="job_editor")
        for i, x in enumerate(ex.get("experiences") or []):
            with st.expander(f"Tasks and results: {x.get('title', '')} · {x.get('employer', '')}"):
                st.text_area("One per line", "\n".join(x.get("achievements") or []), key=f"ach{i}", height=150)
    with st.container(border=True):
        st.markdown("**Education**")
        edu = st.data_editor(pd.DataFrame([{k: x.get(k, "") for k in ("degree", "field", "institution", "completed")}
                                           for x in ex.get("education") or []]),
                             hide_index=True, num_rows="dynamic", key="edu_editor")
    if ex.get("questions"):
        with st.expander(f"{len(ex['questions'])} open question(s) from your CV", expanded=True):
            for q in ex["questions"]:
                st.markdown(f"- {q['question']}" + (f" *(CV: {q['cv_says']})*" if q.get("cv_says") else ""))
            st.caption("Fix what you can above. Anything left is saved as a question in your profile.")

    lang_rows = [r for r in langs.to_dict("records") if str(r.get("language") or "").strip()]
    missing = [r["language"] for r in lang_rows if not r.get("level")]
    if missing:
        st.info(f"Choose a level for: {', '.join(missing)}")
    replace = True
    if cfg.profile_path.exists():
        replace = st.checkbox("Replace my existing profile (a backup copy is kept)")
    c = st.columns([1, 2, 3])
    if c[0].button("Back", icon=":material/arrow_back:"):
        st.session_state["setup_step"] -= 1
        st.rerun()
    if c[1].button("Save my profile", type="primary", icon=":material/save:",
                   disabled=bool(missing) or not lang_rows or not name.strip() or not replace):
        ex["experiences"] = [{k: ("" if pd.isna(v) else v) for k, v in r.items()}
                             | {"achievements": [a for a in st.session_state.get(f"ach{i}", "").splitlines() if a.strip()]}
                             for i, r in enumerate(jobs.to_dict("records"))]
        ex["education"] = [{k: ("" if pd.isna(v) else v) for k, v in r.items()}
                           | {"details": (ex.get("education") or [{}] * (i + 1))[i].get("details", [])
                              if i < len(ex.get("education") or []) else []}
                           for i, r in enumerate(edu.to_dict("records"))]
        photo = cv_import.find_photo(cfg.root / cfg.settings.paths.cv_dir)
        try:
            prof = cv_import.build_profile(
                ex, languages=[{"language": r["language"], "level": r["level"], "certificate": r.get("certificate") or ""}
                               for r in lang_rows],
                personal={"full_name": name.strip(), "email": email.strip(), "phone": phone.strip(),
                          "nationalities": [x.strip() for x in nat.split(",") if x.strip()],
                          "date_of_birth": dob.strip(), "right_to_work": {"Yes": True, "No": False}.get(rtw),
                          "right_to_work_note": rtw_note.strip()},
                photo_path=str(photo.relative_to(cfg.root)).replace("\\", "/") if photo else None,
                version=_next_version(cfg))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Not saved: {exc}")
            return
        _backup(cfg.profile_path)
        cfg.profile_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.profile_path.write_text(cv_import.profile_yaml(prof), encoding="utf-8")
        st.session_state["setup_step"] += 1
        st.rerun()


def _next_version(cfg: AppConfig) -> int:
    try:
        return load_profile(cfg.profile_path).profile_version + 1
    except Exception:  # noqa: BLE001 - no or unreadable profile
        return 1


def _backup(path: Path) -> None:
    if path.exists():
        n = 1
        while path.with_name(f"{path.stem}.backup{n}{path.suffix}").exists():
            n += 1
        path.with_name(f"{path.stem}.backup{n}{path.suffix}").write_bytes(path.read_bytes())


def _step_search(cfg: AppConfig) -> None:
    st.subheader("3 · Where and what you are looking for", anchor=False)
    s = cfg.settings
    home = load_yaml(cfg.home_location_path) if cfg.home_location_path.exists() else {}
    with st.form("search_form"):
        c = st.columns(3)
        country = c[0].text_input("Country you want to work in", home.get("country") or "")
        city = c[1].text_input("Your home city", home.get("city") or "")
        postcode = c[2].text_input("Postcode (optional)", home.get("postcode") or "")
        street = st.text_input("Street and number (optional, only printed in your own CV/letter header)",
                               home.get("street") or "")
        c = st.columns(2)
        primary = c[0].text_input("Cities or regions to search (comma-separated)", city)
        secondary = c[1].text_input("Also acceptable (optional)", "")
        wanted = st.text_area("What kind of job are you looking for? (your own words)",
                              placeholder="e.g. lab technician or research assistant in microbiology or food safety")
        c = st.columns(3)
        salary = c[0].number_input("Minimum gross yearly salary (0 = any)", min_value=0, step=1000, value=0)
        currency = c[1].text_input("Currency", s.preferences.salary_currency)
        doc_langs = c[2].multiselect("CV and letter languages", ["en", "de", "it"], default=["en"])
        jobspy = st.toggle("Also search LinkedIn, Indeed and Glassdoor", value=s.collection.allow_jobspy,
                           help="Through JobSpy, without your account. Their terms of use forbid automatic "
                                "collection and they may briefly block your connection. Recommended for most people.")
        go = st.form_submit_button("Configure my search (about 1 minute)", type="primary",
                                   icon=":material/auto_awesome:")
    if go:
        if not (country.strip() and city.strip() and primary.strip()):
            st.error("Country, home city and at least one city or region are needed.")
            return
        files.update_yaml(cfg.home_location_path, {"street": street.strip() or None, "postcode": postcode.strip() or None,
                                                   "city": city.strip(), "country": country.strip()})
        answers = SearchAnswers(country=country.strip(), wanted_jobs=wanted.strip(),
                                primary_locations=[x.strip() for x in primary.split(",") if x.strip()],
                                secondary_locations=[x.strip() for x in secondary.split(",") if x.strip()],
                                min_salary=float(salary), currency=currency.strip() or "EUR",
                                document_languages=doc_langs or ["en"])
        try:
            with st.spinner("Claude is drafting search terms, location filters and CV styles for you..."):
                d = draft_search(_llm(cfg), load_profile(cfg.profile_path), s, answers)
                notes = apply_search(cfg.root, cfg.profile_path, d, answers)
            save_settings(cfg, {"collection.allow_jobspy": bool(jobspy)})
        except Exception as exc:  # noqa: BLE001
            st.error(f"The search could not be configured: {exc}")
            return
        st.session_state["search_done"] = {"focus": d.get("career_focus", ""), "notes": notes,
                                           "strategies": d.get("cv_strategies", [])}
    done = st.session_state.get("search_done")
    if done:
        st.success("Search configured: " + "; ".join(done["notes"]))
        st.markdown(f"**Your field:** {done['focus']}")
        for x in done["strategies"]:
            st.markdown(f"- CV style **{x['letter']}**: {x['description']}")
        with st.expander("See the search terms"):
            st.code((cfg.root / "config" / "search_terms.yaml").read_text(encoding="utf-8"), language="yaml")
    _nav(next_ok=bool(done) or (cfg.root / "config" / "search_terms.yaml").exists() and cfg.home_location_path.exists())


def email_form(cfg: AppConfig, key: str) -> None:
    """Shared by the wizard and the Settings page."""
    env = files.env_status(cfg.root / ".env")
    profile_email = ""
    if cfg.profile_path.exists():
        try:
            profile_email = load_profile(cfg.profile_path).personal.email or ""
        except Exception:  # noqa: BLE001
            pass
    with st.form(f"email_{key}"):
        to = st.text_input("Send my reports to", env.get("NOTIFY_TO") or env.get("SMTP_USER") or profile_email)
        sender = st.text_input("Send them from this email account (can be the same address)",
                               env.get("SMTP_USER") or to)
        host_guess = PROVIDERS.get(sender.split("@")[-1].lower(), (env.get("SMTP_HOST") or "", 587))
        c = st.columns(2)
        host = c[0].text_input("Mail server (leave empty for Gmail, GMX, web.de, Yahoo, iCloud, Outlook)",
                               env.get("SMTP_HOST") or host_guess[0])
        port = c[1].selectbox("Port", [587, 465], index=0 if int(env.get("SMTP_PORT") or host_guess[1]) == 587 else 1)
        st.info(APP_PASSWORD_HELP.get(host) or APP_PASSWORD_HELP["smtp.gmail.com"], icon=":material/key:")
        pw = st.text_input("App password" + (" (saved - leave empty to keep it)" if env.get("SMTP_PASSWORD") else ""),
                           type="password")
        st.caption("Stored only in the `.env` file on this computer. Reports go only to you, never to employers.")
        c = st.columns(2)
        save = c[0].form_submit_button("Save", type="primary", icon=":material/save:")
        test = c[1].form_submit_button("Save and send a test email", icon=":material/send:")
    if save or test:
        known = PROVIDERS.get(sender.strip().split("@")[-1].lower())
        if not host.strip() and known:
            host, port = known                      # filled in from the sender's address
        if "@" not in to or "@" not in sender or not host.strip():
            st.error("Please fill in both addresses, and the mail server if your provider is not listed.")
            return
        files.update_env(cfg.root / ".env", {"NOTIFY_TO": to.strip(), "SMTP_USER": sender.strip(),
                                             "SMTP_HOST": host.strip(), "SMTP_PORT": str(port),
                                             "SMTP_PASSWORD": pw})
        save_settings(cfg, {"automation.email_enabled": True})
        st.success("Email settings saved.")
        if test:
            _send_test(cfg)


def _send_test(cfg: AppConfig) -> None:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from vjh.automation.notify import NotifyError, build_message, mail_config, send
    from vjh.automation.report import build_daily_report
    from vjh.db import migrate
    try:
        mc = mail_config()
        if mc is None:
            st.error("The app password is missing.")
            return
        migrate.upgrade(cfg.db_path)
        engine = migrate.make_engine(cfg.db_path)
        try:
            with Session(engine) as s:
                rep = build_daily_report(s, datetime.now(), cfg.settings)
        finally:
            engine.dispose()
        rep.subject_prefix = "[TEST] "
        with st.spinner("Sending..."):
            send(build_message(rep, mc), mc)
        st.success(f"Test email sent to {mc.to}. Check your inbox (and the spam folder).")
    except NotifyError as exc:
        st.error(f"The test email failed: {exc}. Check the address, server and app password.")


def schedule_form(cfg: AppConfig, key: str) -> None:
    a = cfg.settings.automation
    current = next((k for k, v in schedule.FREQUENCIES.items() if v == list(a.run_days)), "Every day")
    with st.form(f"sched_{key}"):
        options = [*schedule.FREQUENCIES, "Don't run automatically"]
        freq = st.radio("How often should Job Hunter look for new jobs?", options, index=options.index(current))
        c = st.columns(2)
        hh, mm = map(int, a.daily_time.split(":"))
        when = c[0].time_input("At what time", time(hh, mm), step=900)
        quiet = c[1].toggle("Email me even on days with nothing new", value=not a.email_only_when_news)
        st.caption("Your computer must be on (it may be asleep: a missed run starts when it wakes up). "
                   "Each run uses your Claude plan for the new jobs only.")
        go = st.form_submit_button("Save schedule", type="primary", icon=":material/schedule:")
    if go:
        hhmm = f"{when.hour:02d}:{when.minute:02d}"
        days = schedule.FREQUENCIES.get(freq, list(a.run_days))
        if not save_settings(cfg, {"automation.daily_time": hhmm, "automation.run_days": days,
                                   "automation.email_only_when_news": not quiet}):
            return
        try:
            if freq == "Don't run automatically":
                schedule.remove(a.task_name)
                st.success("Automatic runs are off. Start a run yourself from the Settings page.")
            else:
                schedule.install(a.task_name, hhmm, cfg.root, cfg.data_dir, days=days)
                st.success(f"Scheduled at {hhmm} {schedule.describe_days(days)} (Windows task '{a.task_name}').")
        except (schedule.ScheduleError, OSError) as exc:
            st.error(f"Windows did not accept the task: {exc}")
    info = schedule.status(a.task_name) if os.name == "nt" else None
    if info:
        st.caption(f"Windows task: {info.get('status', '?')} · next run {info.get('next_run', '?')}")


def _step_email(cfg: AppConfig) -> None:
    st.subheader("4 · Where to send your reports", anchor=False)
    st.caption("A short email after each run: the best new jobs and anything due. You can skip this.")
    email_form(cfg, "wizard")
    _nav(next_label="Next (or skip)")


def _step_schedule(cfg: AppConfig) -> None:
    st.subheader("5 · How often to search", anchor=False)
    schedule_form(cfg, "wizard")
    _nav()


def initialise(cfg: AppConfig) -> list[str]:
    """Same as `vjh init`: folders, database, evidence and sources. Returns short lines."""
    from sqlalchemy.orm import Session

    from vjh.db import migrate
    from vjh.profile.registry import sync_evidence
    from vjh.sources.registry import load_registry, sync_sources
    for sub in DATA_SUBDIRS:
        (cfg.data_dir / sub).mkdir(parents=True, exist_ok=True)
    migrate.upgrade(cfg.db_path)
    engine = migrate.make_engine(cfg.db_path)
    try:
        with Session(engine) as s, s.begin():
            n_ev = sync_evidence(s, load_profile(cfg.profile_path))
            n_src = sync_sources(s, load_registry(cfg.root))
    finally:
        engine.dispose()
    return [f"database ready ({cfg.db_path})", f"{n_ev} facts from your profile", f"{n_src} job sources"]


def _step_finish(cfg: AppConfig) -> None:
    st.subheader("6 · Finish", anchor=False)
    try:
        lines = initialise(cfg)
        st.success("Ready: " + " · ".join(lines))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Setup is not complete: {exc}")
        _nav(next_label=None)
        return
    c = st.columns(2)
    with c[0].container(border=True):
        st.markdown("**Desktop icon**  \nOpens this dashboard with a double-click.")
        if st.button("Create the Desktop icon", icon=":material/desktop_windows:"):
            from vjh.automation import shortcut
            try:
                st.success(f"Created: {shortcut.install(cfg.root, cfg.data_dir, cfg.settings.dashboard.title)}")
            except RuntimeError as exc:
                st.error(str(exc))
    with c[1].container(border=True):
        st.markdown("**First search**  \nCollects jobs, filters them and lets Claude rate the new ones "
                    f"(at most {cfg.settings.llm.subscription_daily_max_jobs} today). Takes 10-30 minutes.")
        if st.button("Start the first search now", icon=":material/play_arrow:", type="primary"):
            _new_console([sys.executable, "-m", "vjh.cli", "run-daily", "--log"], cfg.root)
            st.success("Started in a separate window. Results appear on the Jobs page as they come in.")
    st.markdown("**Optional, any time:** ask Claude Code to *find more job sources for my area* "
                "(company career pages, university job boards): it researches and checks them with you.")
    c = st.columns([1, 2, 3])
    if c[0].button("Back", icon=":material/arrow_back:"):
        st.session_state["setup_step"] -= 1
        st.rerun()
    c[1].button("Go to the dashboard", type="primary", icon=":material/dashboard:", on_click=finish_setup)


# ---------------- Settings page ----------------

def page_settings(cfg: AppConfig) -> None:
    st.header(":material/settings: Settings", anchor=False)
    st.caption("Changes are saved to config\\settings.yaml (and .env for email) and apply from the next run.")
    t = st.tabs(["Profile and CV", "Search", "Email", "Schedule", "AI and documents", "Dashboard"])
    with t[0]:
        _settings_profile(cfg)
    with t[1]:
        _settings_search(cfg)
    with t[2]:
        email_form(cfg, "settings")
    with t[3]:
        schedule_form(cfg, "settings")
        if st.button("Run now", icon=":material/play_arrow:"):
            _new_console([sys.executable, "-m", "vjh.cli", "run-daily", "--log"], cfg.root)
            st.success("Started in a separate window.")
    with t[4]:
        _settings_ai(cfg)
    with t[5]:
        _settings_dashboard(cfg)


def _settings_profile(cfg: AppConfig) -> None:
    cv_dir = cfg.root / cfg.settings.paths.cv_dir
    cv, photo = cv_import.find_cv(cv_dir), cv_import.find_photo(cv_dir)
    st.markdown(f"**CV:** `{cv.name if cv else 'none'}` · **photo:** `{photo.name if photo else 'none'}`")
    c = st.columns(2)
    up = c[0].file_uploader("Upload a new CV", type=["pdf", "docx"], key="set_cv")
    if up is not None:
        st.success(f"Saved `{cv_import.save_upload(cv_dir, up.name, up.getvalue()).name}`. "
                   "Use *Rebuild my profile* to read it.")
    ph = c[1].file_uploader("Upload a new photo", type=["jpg", "jpeg", "png"], key="set_photo")
    if ph is not None:
        p = cv_import.save_upload(cv_dir, ph.name, ph.getvalue())
        if cfg.profile_path.exists():
            prof = yaml.safe_load(cfg.profile_path.read_text(encoding="utf-8"))
            prof["personal"]["photo_path"] = str(p.relative_to(cfg.root)).replace("\\", "/")
            _write_profile(cfg, prof)
        st.success(f"Photo saved and used from now on: `{p.name}`")
    if not cfg.profile_path.exists():
        st.info("No profile yet: run the setup.")
        return
    try:
        prof = yaml.safe_load(cfg.profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        st.error(f"master_profile.yaml cannot be read: {exc}")
        return
    st.markdown("**Languages** (the highest level any CV or letter may claim)")
    st.caption(LEVEL_HELP)
    rows = [{"language": x["language"], "level": x["level"], "certificate": x.get("certificate") or ""}
            for x in prof.get("languages") or []]
    edited = st.data_editor(pd.DataFrame(rows), num_rows="dynamic", hide_index=True, key="set_langs",
                            column_config={"level": st.column_config.SelectboxColumn(
                                "level", options=cv_import.LEVELS, required=True)})
    if st.button("Save languages", icon=":material/save:"):
        try:
            set_languages(prof, [r for r in edited.to_dict("records") if str(r.get("language") or "").strip()])
            _write_profile(cfg, prof)
            st.success("Languages saved.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Not saved: {exc}")
    if prof.get("todos"):
        with st.expander(f"{len(prof['todos'])} open question(s) about your profile"):
            for q in prof["todos"]:
                st.markdown(f"- {q['question']}" + (f" *(CV: {q['cv_says']})*" if q.get("cv_says") else ""))
            st.caption("Answer them by editing the profile file, or ask Claude Code: *go through my profile questions*.")
    c = st.columns(3)
    if c[0].button("Open the profile file", icon=":material/edit_document:"):
        _open(cfg.profile_path)
    c[1].button("Rebuild my profile from the CV", icon=":material/auto_awesome:", on_click=open_setup, args=(0,))
    if c[2].button("Open the cv folder", icon=":material/folder_open:"):
        _open(cv_dir)


def set_languages(prof: dict, rows: list[dict]) -> None:
    """Replace the languages and their evidence items with what the user confirmed."""
    old = {i for x in prof.get("languages") or [] for i in x.get("evidence_ids") or []}
    prof["evidence"] = [e for e in prof.get("evidence") or [] if e["id"] not in old]
    langs = []
    for n, r in enumerate(rows, 1):
        level, cert = r["level"], (r.get("certificate") or "").strip() or None
        if level not in cv_import.LEVELS:
            raise ValueError(f"choose a level for {r['language']}")
        eid = f"EV-LANG-{n:03d}"
        while any(e["id"] == eid for e in prof["evidence"]):
            n += 100
            eid = f"EV-LANG-{n:03d}"
        claim = f"{r['language']}: {level}" + (f", {cert}" if cert else (", no certificate" if level != "native" else ""))
        prof["evidence"].append({"id": eid, "claim": claim, "supporting_experience": "languages", "source": "user",
                                 "confidence": "high", "verification_status": "verified"})
        langs.append({"language": r["language"].strip(), "level": level, "certified": bool(cert),
                      "certificate": cert, "evidence_ids": [eid]})
    prof["languages"] = langs


def _write_profile(cfg: AppConfig, prof: dict) -> None:
    from vjh.models.profile import MasterProfile
    MasterProfile.model_validate(prof)
    text = cfg.profile_path.read_text(encoding="utf-8")
    head = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    _backup(cfg.profile_path)
    cfg.profile_path.write_text(head + "\n" + yaml.safe_dump(prof, allow_unicode=True, sort_keys=False, width=110),
                                encoding="utf-8")


def _open(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows
    except (AttributeError, OSError) as exc:
        st.error(f"Could not open {path}: {exc}")


def _settings_search(cfg: AppConfig) -> None:
    s = cfg.settings
    with st.form("set_search"):
        c = st.columns(2)
        primary = c[0].text_input("Cities or regions", ", ".join(s.preferences.primary_locations))
        secondary = c[1].text_input("Also acceptable", ", ".join(s.preferences.secondary_locations))
        c = st.columns(4)
        salary = c[0].number_input("Minimum yearly salary (0 = any)", min_value=0, step=1000,
                                   value=int(s.preferences.min_gross_annual_salary))
        rec = c[1].slider("Recommend jobs from score", 0, 100, int(s.thresholds.recommend_min_score))
        docs = c[2].slider("Write CV + letter from score", 0, 100, int(s.thresholds.generate_documents_min_score))
        age = c[3].number_input("Ignore ads older than (days)", 1, 365, s.thresholds.max_job_age_days)
        doc_langs = st.multiselect("CV and letter languages", ["en", "de", "it"], default=s.preferences.document_languages)
        jobspy = st.toggle("Search LinkedIn, Indeed and Glassdoor (JobSpy)", value=s.collection.allow_jobspy)
        go = st.form_submit_button("Save", type="primary", icon=":material/save:")
    if go and save_settings(cfg, {
            "preferences.primary_locations": [x.strip() for x in primary.split(",") if x.strip()],
            "preferences.secondary_locations": [x.strip() for x in secondary.split(",") if x.strip()],
            "preferences.min_gross_annual_salary": float(salary), "thresholds.recommend_min_score": rec,
            "thresholds.generate_documents_min_score": docs, "thresholds.max_job_age_days": int(age),
            "preferences.document_languages": doc_langs or ["en"], "collection.allow_jobspy": bool(jobspy)}):
        st.success("Saved.")
    path = cfg.root / "config" / "search_terms.yaml"
    with st.expander("Search terms (advanced)"):
        text = st.text_area("search_terms.yaml", path.read_text(encoding="utf-8") if path.exists() else "", height=320)
        c = st.columns(2)
        if c[0].button("Save search terms", icon=":material/save:"):
            try:
                files.replace_yaml_text(path, text, validate=lambda d: _check_terms(d, s))
                st.success("Search terms saved.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Not saved: {exc}")
        c[1].button("Redo the search setup with Claude", icon=":material/auto_awesome:", on_click=open_setup, args=(2,))


def _check_terms(d: dict, s: Settings) -> None:
    from vjh.pipeline.stage1 import Rules
    Rules.from_settings(d, s)            # compiles every pattern; raises on a broken one


def _settings_ai(cfg: AppConfig) -> None:
    llm, a = cfg.settings.llm, cfg.settings.automation
    if llm.backend == "claude_code":
        _claude_login_box(cfg)
    else:
        st.caption("Using the Anthropic API key in .env.")
    models = list(llm.prices) or [llm.strong_model]
    with st.form("set_ai"):
        c = st.columns(2)
        screen = c[0].selectbox("Model for rating jobs", models, index=models.index(llm.screening_model)
                                if llm.screening_model in models else 0)
        strong = c[1].selectbox("Model for CVs and letters", models, index=models.index(llm.strong_model)
                                if llm.strong_model in models else 0)
        c = st.columns(3)
        max_jobs = c[0].number_input("Jobs rated per day (protects your plan's limits)", 1, 500,
                                     llm.subscription_daily_max_jobs)
        auto = c[1].toggle("Write CV + letter automatically for top matches", value=a.auto_documents)
        per_day = c[2].number_input("... at most per day", 0, 10, a.auto_documents_max_per_day)
        revise = st.toggle("Extra editing pass for natural cover letters (+1 AI call per letter)",
                           value=llm.letter_revision)
        go = st.form_submit_button("Save", type="primary", icon=":material/save:")
    if go and save_settings(cfg, {"llm.screening_model": screen, "llm.strong_model": strong,
                                  "llm.subscription_daily_max_jobs": int(max_jobs), "automation.auto_documents": auto,
                                  "automation.auto_documents_max_per_day": int(per_day),
                                  "llm.letter_revision": revise}):
        st.success("Saved.")


def _settings_dashboard(cfg: AppConfig) -> None:
    d, g = cfg.settings.dashboard, cfg.settings.progress
    with st.form("set_dash"):
        c = st.columns(3)
        title = c[0].text_input("Name (browser tab, menu and Desktop icon)", d.title)
        goal = c[1].number_input("Applications you aim for per week (0 = no goal)", 0, 100, g.weekly_application_goal)
        port = c[2].number_input("Port (only change it for a second copy on this PC)", 1024, 65535, d.port)
        go = st.form_submit_button("Save", type="primary", icon=":material/save:")
    if go and save_settings(cfg, {"dashboard.title": title.strip() or d.title,
                                  "progress.weekly_application_goal": int(goal), "dashboard.port": int(port)}):
        st.success("Saved. A new name or port applies after restarting the dashboard.")
