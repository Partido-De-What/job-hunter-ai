"""Phase 4: evidence gate, rendering, QA checks and the generation flow (fake Claude, fake Word)."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from conftest import EXAMPLE_ROOT
from sqlalchemy import select
from sqlalchemy.orm import Session

from vjh.config import load_config
from vjh.db import migrate
from vjh.db.tables import GeneratedDocumentRow, JobRow
from vjh.documents.generate import generate_for_job
from vjh.documents.plan import (
    CVPlan,
    EducationBlock,
    ExperienceBlock,
    Item,
    LetterPlan,
    Paragraph,
    SkillGroup,
    enforce_cv,
    enforce_letter,
)
from vjh.documents.qa import language_level_problems, python_checks
from vjh.documents.render import clean_location, docx_text, render_cv, render_letter
from vjh.llm.client import LLMResult
from vjh.models import MasterProfile
from vjh.models.profile import HomeLocation

SETTINGS = load_config(EXAMPLE_ROOT).settings.model_copy(deep=True)
SETTINGS.llm.backend = "claude_code"
HOME = HomeLocation(street="Teststraße 1", postcode="1010", city="Vienna")


@pytest.fixture
def profile(profile_data) -> MasterProfile:
    profile_data["personal"].update(date_of_birth="1990-03-04", nationalities=["Italian"],
                                    right_to_work=True, right_to_work_note="EU citizen", email="t@example.com")
    profile_data["experiences"].append({"id": "exp_new", "employer": "New GmbH", "title": "Technician",
                                        "start": "2024-08", "end": "2025-02", "evidence_ids": ["EV-LAB-001"]})
    return MasterProfile.model_validate(profile_data)


def cv_plan(**kw) -> CVPlan:
    base = dict(summary=Item(text="Lab technician with PCR experience.", evidence_ids=["EV-LAB-001"]),
                experience=[ExperienceBlock(experience_id="exp_lab",
                                            bullets=[Item(text="Ran PCR assays", evidence_ids=["EV-LAB-001"]),
                                                     Item(text="Ran HPLC", evidence_ids=["EV-FAKE-001"])])],
                education=[EducationBlock(education_id="edu_msc", bullets=[])],
                skills=[SkillGroup(group="Lab", items=[Item(text="PCR", evidence_ids=["EV-LAB-001"]),
                                                       Item(text="Invented skill", evidence_ids=[])])])
    return CVPlan(**{**base, **kw})


# ---------------- evidence gate ----------------

def test_cv_gate_drops_unsupported_and_adds_missing_jobs_in_date_order(profile):
    plan = cv_plan()
    notes = enforce_cv(plan, profile)
    texts = [b.text for blk in plan.experience for b in blk.bullets]
    assert "Ran HPLC" not in texts and "Ran PCR assays" in texts
    assert [i.text for i in plan.skills[0].items] == ["PCR"]
    assert [b.experience_id for b in plan.experience] == ["exp_new", "exp_lab"]   # newest first
    assert any("dropped" in n for n in notes) and any("missing" in n for n in notes)


def test_relevant_experience_is_emphasised(profile):
    from vjh.documents.qa import emphasis_issues
    many = [Item(text=f"Did PCR {i}", evidence_ids=["EV-LAB-001"]) for i in range(8)]
    plan = cv_plan(focus_experience_ids=["exp_lab"],
                   experience=[ExperienceBlock(experience_id="exp_lab", bullets=list(many)),
                               ExperienceBlock(experience_id="exp_new", bullets=list(many))])
    enforce_cv(plan, profile)
    counts = {b.experience_id: len(b.bullets) for b in plan.experience}
    assert counts == {"exp_lab": 6, "exp_new": 2}            # focus detailed, other condensed
    assert [b.experience_id for b in plan.experience] == ["exp_new", "exp_lab"]   # still newest first
    assert emphasis_issues(plan) == []
    plan.experience[0].bullets = list(many)                  # less relevant role grows bigger
    assert any("more detail" in i.text for i in emphasis_issues(plan))


def test_focus_role_defaults_to_most_detailed_when_missing(profile):
    plan = cv_plan(focus_experience_ids=["not_a_role"])
    notes = enforce_cv(plan, profile)
    assert plan.focus_experience_ids == ["exp_lab"] and any("no focus role" in n for n in notes)


def test_letter_gate_keeps_motivation_without_evidence(profile):
    plan = LetterPlan(subject="Application", salutation="Dear Hiring Team,", sign_off="Kind regards", paragraphs=[
        Paragraph(kind="evidence", text="I ran HPLC daily.", evidence_ids=[]),
        Paragraph(kind="evidence", text="I ran PCR assays.", evidence_ids=["EV-LAB-001"]),
        Paragraph(kind="motivation", text="Your vaccines matter.", evidence_ids=[])])
    enforce_letter(plan, profile)
    assert [p.text for p in plan.paragraphs] == ["I ran PCR assays.", "Your vaccines matter."]


# ---------------- rendering ----------------

def test_cv_render_uses_profile_facts_not_ai(profile, tmp_path):
    plan = cv_plan()
    enforce_cv(plan, profile)
    path = render_cv(plan, profile, HOME, "de", tmp_path / "cv.docx", photo=None, city_names={"de": "Wien"})
    text = docx_text(path)
    assert "Test Person" in text and "Teststraße 1, 1010 Wien" in text
    assert "04.03.1990" in text and "Italienisch" in text
    assert "Example Lab GmbH" in text and "01/2023 - 06/2024" in text
    assert "Deutsch: A2" in text and "C1" in text
    assert "Arbeitserlaubnis: EU citizen" in text
    assert language_level_problems(text, profile, ALIASES) == []


def test_letter_render_and_location_cleanup(profile, tmp_path):
    plan = LetterPlan(subject="Bewerbung: Labortechniker", salutation="Sehr geehrte Damen und Herren,",
                      paragraphs=[Paragraph(kind="opening", text="Hiermit…", evidence_ids=["EV-LAB-001"])],
                      sign_off="Mit freundlichen Grüßen")
    path = render_letter(plan, profile, HOME, "de", tmp_path / "l.docx", "Saat GmbH", "Wien, Wien, Austria",
                         date(2026, 11, 15), compact=2, city_names={"de": "Wien"})
    text = docx_text(path)
    assert "Wien, 15. November 2026" in text and "Saat GmbH" in text and "Wien, Austria" in text
    assert clean_location("Vienna, Vienna, Austria") == "Vienna, Austria"


def test_private_notes_never_reach_ai_or_documents(profile_data, tmp_path):
    from vjh.documents.qa import QA_INSTRUCTIONS
    from vjh.documents.writer import build_writer_system
    from vjh.llm.prompts import build_system_prompt, render_profile
    secret = "Gross monthly salary EUR 9,999 PRIVATE-MARKER"
    profile_data["experiences"][0]["private_notes"] = [secret]
    prof = MasterProfile.model_validate(profile_data)
    prompts = [build_system_prompt(prof, SETTINGS), QA_INSTRUCTIONS + render_profile(prof, SETTINGS)]
    prompts += [build_writer_system(prof, SETTINGS, k, lang) for k in ("cv", "letter") for lang in ("en", "de")]
    prompts += [build_writer_system(prof, SETTINGS, "letter", "en", revision=True)]
    for p in prompts:
        assert "PRIVATE-MARKER" not in p and "9,999" not in p
    plan = cv_plan()
    enforce_cv(plan, prof)
    cv = docx_text(render_cv(plan, prof, HOME, "en", tmp_path / "cv.docx", photo=None))
    letter_plan = LetterPlan(subject="A", salutation="Dear Hiring Team,", sign_off="Kind regards",
                             paragraphs=[Paragraph(kind="opening", text="Hello", evidence_ids=["EV-LAB-001"])])
    letter = docx_text(render_letter(letter_plan, prof, HOME, "en", tmp_path / "l.docx", "E", None, date(2026, 11, 16)))
    assert "PRIVATE-MARKER" not in cv and "PRIVATE-MARKER" not in letter


# ---------------- QA ----------------

ALIASES = SETTINGS.candidate.language_aliases


@pytest.mark.parametrize("text,bad", [
    ("German: A2 - actively learning", False),
    ("I speak English fluently and am learning German (A2).", False),
    ("Fluent in English and German.", True),
    ("Deutsch: B2", True),
    ("Gute Deutschkenntnisse", True),
    ("English: C1 | German: A2", False),
    ("English: C1 - fluent", False),          # English is C1 in the fake profile: "fluent" is fair at C1
    ("English: C2", True),                    # but C2 is above it
    ("English: native speaker", True)
])
def test_language_level_detector(profile, text, bad):
    assert bool(language_level_problems(text, profile, ALIASES)) is bad


def test_language_levels_work_for_any_language(profile_data):
    profile_data["languages"].append({"language": "Spanish", "level": "B1", "evidence_ids": ["EV-LANG-001"]})
    prof = MasterProfile.model_validate(profile_data)
    assert language_level_problems("Spanish: fluent", prof, ALIASES)
    assert language_level_problems("Español: C1", prof, ALIASES)
    assert not language_level_problems("Spanish: B1", prof, ALIASES)


def test_cv_conventions_hide_photo_and_birth_date(profile, tmp_path):
    from vjh.models.settings import CVConventions
    plan = cv_plan()
    enforce_cv(plan, profile)
    text = docx_text(render_cv(plan, profile, HOME, "en", tmp_path / "cv.docx", photo=None,
                               conventions=CVConventions(date_of_birth=False, nationality=False)))
    assert "1990" not in text and "Nationality" not in text


def test_python_checks(profile):
    good = "Example Lab GmbH 01/2023 New GmbH 08/2024 PCR"
    assert python_checks(good, kind="cv", lang="en", profile=profile, employer="X", pages=2, writer_notes=[]) == []
    issues = python_checks("She ran PCR at Example Lab GmbH 01/2023 [insert]", kind="cv", lang="en", profile=profile,
                           employer="X", pages=3, writer_notes=["dropped x"])
    cats = {(i.severity, i.category) for i in issues}
    assert ("error", "wrong_fact") in cats            # pronoun + missing New GmbH
    assert ("error", "ats") in cats                   # 3 pages
    assert ("error", "other") in cats                 # placeholder
    assert ("warning", "unsupported_claim") in cats   # writer note
    letter = python_checks("Dear team, I want to join you.", kind="cover_letter", lang="en", profile=profile,
                           employer="Example Pharma GmbH", pages=1, writer_notes=[])
    assert any("not named" in i.text for i in letter)


# ---------------- full flow with fake Claude and fake Word ----------------

class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.systems = []

    def complete_json(self, *, system, user, schema, model):
        self.systems.append(system)
        return LLMResult(text=json.dumps(self.replies.pop(0)), stop_reason="end_turn", model=model)


def cv_reply():
    return cv_plan().model_dump(mode="json") | {"keywords_used": ["PCR"], "flags": ["PCR depth may be asked"]}


def letter_reply():
    return {"subject": "Application: Lab Technician", "salutation": "Dear Hiring Team,", "sign_off": "Kind regards",
            "flags": [], "paragraphs": [
                {"kind": "opening", "text": "I am applying to Saat GmbH as a lab technician.", "evidence_ids": ["EV-LAB-001"]},
                {"kind": "motivation", "text": "Your breeding work interests me.", "evidence_ids": []}]}


def qa_reply(issues=()):
    return {"issues": list(issues), "summary": "ok"}


@pytest.fixture
def env(db_path, profile, tmp_path):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    now = datetime(2026, 11, 15)
    with Session(engine) as s, s.begin():
        s.add(JobRow(title="Lab Technician", employer="Saat GmbH", location="Wien", source="Test",
                     discovered_at=now, retrieved_at=now, description="PCR " * 100, status="evaluated",
                     content_hash="h"))
    yield engine, profile, tmp_path
    engine.dispose()


def fake_pdf(pages_seq):
    pages = iter(pages_seq)

    def convert(docx: Path):
        pdf = docx.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF")
        return pdf, None
    return convert, (lambda pdf: next(pages))


def test_generate_writes_files_db_rows_and_qa(env):
    engine, profile, out = env
    llm = FakeLLM([cv_reply(), qa_reply(), letter_reply(), letter_reply(), qa_reply()])   # letter: draft + edit
    convert, count = fake_pdf([2, 2, 1])          # CV 2 pages; letter 2 then 1 after tightening
    results = generate_for_job(engine, SETTINGS, profile, HOME, llm, job_id=1, langs=["en"],
                               kinds=["cv", "letter"], out_root=out, photo=None, convert_pdf=convert,
                               count_pages=count, today=date(2026, 11, 15))
    assert [r.error for r in results] == [None, None]
    assert all(r.qa.passed for r in results)
    cv, letter = results
    assert cv.docx.name == "Test_Person_CV_Lab_Technician.docx" and letter.qa.pages == 1
    assert letter.docx.name == "Test_Person_Cover_Letter_Lab_Technician.docx"
    assert (cv.docx.parent / "Test_Person_CV_Lab_Technician_qa.json").exists()
    assert any(i.source == "writer" for i in cv.qa.issues)          # writer flags kept as warnings
    assert "German" in llm.systems[0] and "A2" in llm.systems[0]
    with Session(engine) as s:
        rows = s.scalars(select(GeneratedDocumentRow)).all()
        assert {r.approval_status for r in rows} == {"qa_passed"}
        assert "EV-LAB-001" in rows[0].evidence_ids
    # second run -> version 2, version 1 untouched
    llm2 = FakeLLM([cv_reply(), qa_reply()])
    convert, count = fake_pdf([2])
    again = generate_for_job(engine, SETTINGS, profile, HOME, llm2, job_id=1, langs=["en"], kinds=["cv"],
                             out_root=out, photo=None, convert_pdf=convert, count_pages=count)
    assert again[0].docx.name == "Test_Person_CV_Lab_Technician_v2.docx" and cv.docx.exists()


def test_doc_filename_is_name_label_job_title():
    from vjh.documents.generate import doc_filename
    assert doc_filename("Anna Muster", "Laborant*in (m/w/d) Mikrobiologie", "cv", "de", 1) == \
        "Anna_Muster_Lebenslauf_Laborant_Mikrobiologie.docx"
    assert doc_filename("Max Beispiel", "Technische:r Assistent:in – Zellkultur", "letter", "de", 3) == \
        "Max_Beispiel_Anschreiben_Technische_Assistent_Zellkultur_v3.docx"
    assert doc_filename("Jörg Weiß", "QC Analyst / Microbiology", "letter", "en", 1) == \
        "Jorg_Weiss_Cover_Letter_QC_Analyst_Microbiology.docx"
    long = doc_filename("A B", "Word " * 40, "cv", "en", 1)
    assert len(long) < 90 and long.endswith("Word.docx")


def test_reviewer_error_fails_qa(env):
    engine, profile, out = env
    bad = {"severity": "error", "category": "unsupported_claim", "text": "claims HPLC", "suggestion": "remove"}
    llm = FakeLLM([cv_reply(), qa_reply([bad])])
    convert, count = fake_pdf([2])
    r = generate_for_job(engine, SETTINGS, profile, HOME, llm, job_id=1, langs=["en"], kinds=["cv"],
                         out_root=out, photo=None, convert_pdf=convert, count_pages=count)[0]
    assert not r.qa.passed
    with Session(engine) as s:
        assert s.scalar(select(GeneratedDocumentRow)).approval_status == "qa_failed"


def test_approve_blocked_when_qa_failed(fake_project, capsys):
    from vjh.cli import main
    main(["init"])
    cfg = load_config()
    engine = migrate.make_engine(cfg.db_path)
    with Session(engine) as s, s.begin():
        now = datetime(2026, 11, 15)
        s.add(JobRow(title="T", employer="E", source="s", discovered_at=now, retrieved_at=now, description="",
                     status="evaluated", content_hash="h"))
        s.flush()
        s.add(GeneratedDocumentRow(job_id=1, doc_type="cv", language="en", file_path="x.docx", created_at=now,
                                   version=1, approval_status="qa_failed", evidence_ids=[]))
    engine.dispose()
    assert main(["docs", "approve", "1"]) == 1
    assert main(["docs", "approve", "1", "--override"]) == 0
    assert "Nothing was sent" in capsys.readouterr().out


# ---------------- natural cover letters: edit pass and style checks ----------------

def _job():
    from vjh.db.tables import JobRow
    return JobRow(id=1, title="Lab Technician", employer="Saat GmbH", location="Wien", source="Test",
                  description="PCR genotyping in our lab", url=None)


EVAL = {"technical_skill_matches": ["PCR / qPCR", "Genotyping", "PCR / qPCR", "Sterile technique"],
        "best_matching_experience": ["Lab work"], "missing_skills": ["HPLC"]}


def test_letter_gets_key_terms_and_an_edit_pass(profile):
    from vjh.documents.writer import letter_key_terms, write_plan
    assert letter_key_terms(EVAL) == ["PCR / qPCR", "Genotyping", "Sterile technique"]       # deduplicated
    edited = letter_reply() | {"angle": "PCR work", "paragraphs": [
        {"kind": "opening", "text": "Genotyping wheat lines by PCR was my daily work.", "evidence_ids": ["EV-LAB-001"]},
        {"kind": "evidence", "text": "Invented claim.", "evidence_ids": ["EV-NOPE"]},
        {"kind": "motivation", "text": "Your breeding work interests me.", "evidence_ids": []}]}
    llm = FakeLLM([letter_reply(), edited])
    users = []
    orig = llm.complete_json
    llm.complete_json = lambda **kw: (users.append(kw["user"]), orig(**kw))[1]
    plan, notes = write_plan(llm, kind="letter", lang="en", job=_job(), evaluation=EVAL, cv_profile=None,
                             profile=profile, settings=SETTINGS)
    assert "KEY TERMS" in users[0] and "Genotyping" in users[0]
    assert "editor of the cover letter" in llm.systems[1] and "DRAFT TO EDIT" in users[1]
    assert "<letter_style>" in llm.systems[0] and "delve" in llm.systems[0]
    assert plan.paragraphs[0].text.startswith("Genotyping")             # the edited wording is used
    assert all(p.text != "Invented claim." for p in plan.paragraphs)      # evidence gate runs again
    assert any(n.startswith("edit pass:") for n in notes)


def test_letter_edit_failure_keeps_checked_draft(profile):
    from vjh.documents.writer import write_plan
    llm = FakeLLM([letter_reply(), "not a letter"])
    plan, notes = write_plan(llm, kind="letter", lang="en", job=_job(), evaluation=EVAL, cv_profile=None,
                             profile=profile, settings=SETTINGS)
    assert plan.paragraphs[0].text == "I am applying to Saat GmbH as a lab technician."
    assert any("kept the first draft" in n for n in notes)


def test_letter_edit_pass_can_be_switched_off(profile):
    from vjh.documents.writer import write_plan
    settings = SETTINGS.model_copy(deep=True)
    settings.llm.letter_revision = False
    llm = FakeLLM([letter_reply()])
    write_plan(llm, kind="letter", lang="en", job=_job(), evaluation=None, cv_profile=None, profile=profile,
               settings=settings)
    assert len(llm.systems) == 1


MECHANICAL = """Dear Hiring Team,
I am applying for the position of Technical Assistant in your Quality Control team in Vienna today.
Your ad asks for SOP compliance and accurate, timely documentation in a regulated laboratory setting.
I have calibrated, cleaned and maintained equipment such as pH meters, autoclaves, thermocyclers and balances.
I followed safety and quality procedures and kept accurate documentation of all my experiments.
I also hold an MSc in Biotechnology and I am excited about this unique opportunity to grow.
I would welcome the chance to discuss the position at an interview with your team soon.
Kind regards"""

NATURAL = """Dear Hiring Team,
For two years my mornings at a soil laboratory started with soil and water samples that had to be tested before noon.
That routine taught me how much a quality control result depends on careful records and a calibrated meter.
Genotyping wheat lines by PCR at a seed company added the molecular side, since every call fed a selection decision.
Your vaccine release testing would let me bring both habits to work where the stakes are higher still.
Because I already live in Vienna, I could start at short notice and would be glad to meet you.
Kind regards"""


def test_letter_style_check_flags_mechanical_wording():
    from vjh.documents.qa import letter_style_issues
    texts = [i.text for i in letter_style_issues(MECHANICAL, None, ["PCR", "Genotyping"])]
    assert any("Echoes the job ad" in t for t in texts)
    assert any("machine-sounding" in t and "unique opportunity" in t for t in texts)
    assert any("start with 'I'" in t for t in texts)
    assert any("list of tools" in t for t in texts)
    assert any("mentions only 0 of 2" in t for t in texts)
    assert letter_style_issues(NATURAL, None, ["PCR", "Genotyping", "quality control"]) == []
