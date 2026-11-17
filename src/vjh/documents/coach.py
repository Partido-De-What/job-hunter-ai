"""Interview preparation pack and short email drafts (follow-up, thank-you).

Same truth rules as the CV and letter: every answer outline must cite real evidence IDs, or it is
dropped. Nothing is ever sent: the emails are drafts for the user to copy into their own mail.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt
from pydantic import BaseModel, ConfigDict

from ..llm.client import LLM
from ..llm.prompts import build_user_message, render_profile
from ..models.profile import MasterProfile
from ..models.settings import Settings
from .render import ACCENT, _base, _bullet, _heading, _line
from .writer import COMMON_RULES, LANG_NAMES, _call

PREP_LABEL = {"en": "Interview_Prep", "de": "Interview_Vorbereitung", "it": "Preparazione_Colloquio"}
EMAIL_KINDS = {"follow_up": "a polite follow-up about the application (no answer yet)",
               "thank_you": "a short thank-you after the interview"}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PrepQuestion(_M):
    question: str
    why_they_ask: str = ""
    answer_outline: str            # STAR outline built only from the cited evidence
    evidence_ids: list[str] = []


class GapQuestion(_M):
    topic: str                     # a requirement the candidate does not (fully) meet
    likely_question: str
    honest_answer: str             # how to address it truthfully (learning plan, related experience)


class InterviewPrep(_M):
    role_summary: str = ""         # what the job is really about, from the ad
    employer_notes: list[str] = []  # facts about the employer stated IN THE AD only
    likely_questions: list[PrepQuestion] = []
    gap_questions: list[GapQuestion] = []
    questions_to_ask: list[str] = []
    language_tips: list[str] = []
    checklist: list[str] = []


class EmailDraft(_M):
    subject: str
    body: str


def _str_list() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def prep_schema() -> dict:
    q = {"type": "object", "additionalProperties": False,
         "required": ["question", "why_they_ask", "answer_outline", "evidence_ids"],
         "properties": {"question": {"type": "string"}, "why_they_ask": {"type": "string"},
                        "answer_outline": {"type": "string"}, "evidence_ids": _str_list()}}
    g = {"type": "object", "additionalProperties": False, "required": ["topic", "likely_question", "honest_answer"],
         "properties": {"topic": {"type": "string"}, "likely_question": {"type": "string"},
                        "honest_answer": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["role_summary", "employer_notes", "likely_questions", "gap_questions",
                         "questions_to_ask", "language_tips", "checklist"],
            "properties": {"role_summary": {"type": "string"}, "employer_notes": _str_list(),
                           "likely_questions": {"type": "array", "items": q},
                           "gap_questions": {"type": "array", "items": g},
                           "questions_to_ask": _str_list(), "language_tips": _str_list(),
                           "checklist": _str_list()}}


def email_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["subject", "body"],
            "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}}


PREP_RULES = """\
TASK: prepare the candidate for a job interview for this job.
- role_summary: 2-3 sentences on what the job is really about and what the employer most needs.
- employer_notes: up to 5 facts about the employer that the AD states (products, size, sites,
  values). Nothing from outside the ad: you cannot browse, so never guess facts.
- likely_questions: 8-10 questions this employer is likely to ask: role-specific technical
  questions first, then behavioural ones ("Tell me about a time..."). For each, answer_outline is
  a short STAR outline (Situation, Task, Action, Result) in note form, built ONLY from the cited
  evidence. If no evidence fits a question, leave it out.
- gap_questions: for each important requirement the candidate does not fully meet, the question
  that will probably come and an honest way to answer it (related experience, what they are
  doing to learn it). Never pretend the gap does not exist.
- questions_to_ask: 5 good questions the candidate can ask (team, training, first months,
  next steps). No questions about pay in the first round unless the ad gives none.
- language_tips: if the interview may be partly in a language the candidate is still learning,
  practical tips at their real level from the profile (a short prepared self-introduction, asking
  to switch language politely). Never suggest claiming a higher level.
- checklist: 5-7 practical items (documents to bring, test the video link, route and time).
- Write in note form, concise and practical. No flags.
"""

EMAIL_RULES = """\
TASK: write {what} for this job, as an email from the candidate.
- 60-120 words, polite and natural, not stiff. Plain text, no placeholders except
  [Name of contact person] if the contact is unknown.
- Mention the exact job title and one concrete point of fit from the evidence.
- Never claim facts that are not in the profile. Never invent dates of an interview or contact.
- subject: short and specific.
"""


def _system(profile: MasterProfile, settings: Settings, lang: str, rules: str) -> str:
    return (f"You are a careful career coach. Write everything in {LANG_NAMES[lang]}.\n\n"
            f"{COMMON_RULES}\n{rules}\n{render_profile(profile, settings)}")


def _user(job, evaluation: dict | None, extra: str = "") -> str:
    parts = []
    if evaluation:
        parts.append("ASSESSMENT OF THIS JOB (from an earlier evaluation):\n"
                     f"- strengths: {'; '.join(evaluation.get('best_matching_experience', [])[:6])}\n"
                     f"- skill matches: {'; '.join(evaluation.get('technical_skill_matches', [])[:8])}\n"
                     f"- gaps (do NOT claim these): {'; '.join(evaluation.get('missing_skills', [])[:8])}")
    if extra:
        parts.append(extra)
    parts.append(build_user_message(job).replace("Evaluate this job for the candidate.", "Prepare for this job."))
    return "\n\n".join(parts)


def enforce_prep(prep: InterviewPrep, profile: MasterProfile) -> list[str]:
    """Drop answer outlines that cite no real evidence (the evidence gate)."""
    known = {e.id for e in profile.evidence}
    notes, kept = [], []
    for q in prep.likely_questions:
        valid = [i for i in q.evidence_ids if i in known]
        if not valid:
            notes.append(f"dropped a question without valid evidence: '{q.question[:60]}'")
            continue
        q.evidence_ids = valid
        kept.append(q)
    prep.likely_questions = kept
    return notes


def write_interview_prep(llm: LLM, *, job, evaluation: dict | None, profile: MasterProfile,
                         settings: Settings, lang: str = "en") -> tuple[InterviewPrep, list[str]]:
    prep = InterviewPrep.model_validate(_call(llm, _system(profile, settings, lang, PREP_RULES),
                                              _user(job, evaluation), prep_schema(), settings))
    return prep, enforce_prep(prep, profile)


def write_email(llm: LLM, *, kind: str, job, evaluation: dict | None, profile: MasterProfile,
                settings: Settings, lang: str = "en", context: str = "") -> EmailDraft:
    if kind not in EMAIL_KINDS:
        raise ValueError(f"unknown email kind {kind!r}")
    extra = f"CONTEXT FROM THE CANDIDATE (facts they gave you): {context.strip()}" if context.strip() else ""
    rules = EMAIL_RULES.format(what=EMAIL_KINDS[kind])
    return EmailDraft.model_validate(_call(llm, _system(profile, settings, lang, rules),
                                           _user(job, evaluation, extra), email_schema(), settings))


def evidence_text(profile: MasterProfile, ids: list[str]) -> str:
    by_id = {e.id: e.claim for e in profile.evidence}
    return "; ".join(by_id[i] for i in ids if i in by_id)


def render_prep(prep: InterviewPrep, profile: MasterProfile, job, out_path: Path) -> Path:
    doc = Document()
    _base(doc)
    _line(doc, f"Interview preparation: {job.title}", bold=True, size=14, color=ACCENT)
    _line(doc, f"{job.employer} · {profile.personal.full_name} · private notes, not for the employer")
    if prep.role_summary:
        _heading(doc, "What the job is about")
        doc.add_paragraph(prep.role_summary)
    if prep.employer_notes:
        _heading(doc, "About the employer (from the ad)")
        for n in prep.employer_notes:
            _bullet(doc, n)
    if prep.likely_questions:
        _heading(doc, "Likely questions and your answers")
        for i, q in enumerate(prep.likely_questions, 1):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.add_run(f"{i}. {q.question}").bold = True
            if q.why_they_ask:
                doc.add_paragraph(f"Why they ask: {q.why_they_ask}").runs[0].italic = True
            doc.add_paragraph(q.answer_outline)
            src = evidence_text(profile, q.evidence_ids)
            if src:
                doc.add_paragraph(f"Based on: {src}").runs[0].font.size = Pt(8.5)
    if prep.gap_questions:
        _heading(doc, "Gaps to prepare for")
        for g in prep.gap_questions:
            p = doc.add_paragraph()
            p.add_run(f"{g.topic}: ").bold = True
            p.add_run(g.likely_question)
            doc.add_paragraph(g.honest_answer)
    for title, items in (("Questions you can ask", prep.questions_to_ask), ("Language tips", prep.language_tips),
                         ("Checklist", prep.checklist)):
        if items:
            _heading(doc, title)
            for x in items:
                _bullet(doc, x)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def prep_filename(full_name: str, job_title: str, lang: str) -> str:
    from .generate import _GENDER_NOTE, _file_part
    title = _file_part(_GENDER_NOTE.sub(" ", job_title), 60) or "Job"
    return f"{_file_part(full_name, 40) or 'Applicant'}_{PREP_LABEL.get(lang, PREP_LABEL['en'])}_{title}.docx"
