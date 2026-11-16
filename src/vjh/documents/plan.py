"""Content plans returned by the AI, and the evidence gate applied to them.

The AI chooses and phrases content; facts (names, dates, employers, titles, degrees,
language levels, contact data) are added by Python from the master profile.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..models.profile import MasterProfile

MAX_FOCUS_BULLETS = 6     # roles most relevant to the job
MAX_OTHER_BULLETS = 2     # other roles: condensed, but still listed (no gaps)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Item(_M):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = []


class ExperienceBlock(_M):
    experience_id: str
    bullets: list[Item]


class EducationBlock(_M):
    education_id: str
    bullets: list[Item]


class SkillGroup(_M):
    group: str
    items: list[Item]


class CVPlan(_M):
    summary: Item
    focus_experience_ids: list[str] = []   # most relevant roles for THIS job (emphasised)
    experience: list[ExperienceBlock]
    education: list[EducationBlock]
    skills: list[SkillGroup]
    keywords_used: list[str] = []
    flags: list[str] = []          # wording that may be hard to defend in an interview


class ParagraphKind(StrEnum):
    OPENING = "opening"
    EVIDENCE = "evidence"           # factual claims: evidence required
    MOTIVATION = "motivation"       # why this role/employer: no claims about the candidate's past
    CLOSING = "closing"


class Paragraph(_M):
    kind: ParagraphKind
    text: str = Field(min_length=1)
    evidence_ids: list[str] = []


class LetterPlan(_M):
    subject: str
    salutation: str
    paragraphs: list[Paragraph]
    sign_off: str
    flags: list[str] = []


# ---------------- JSON schemas for structured output ----------------

def _item_schema() -> dict:
    return {"type": "object",
            "properties": {"text": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "evidence_ids"], "additionalProperties": False}


def cv_schema(profile: MasterProfile) -> dict:
    item = _item_schema()
    items = {"type": "array", "items": item}
    return {
        "type": "object",
        "properties": {
            "summary": item,
            "focus_experience_ids": {"type": "array",
                                     "items": {"type": "string", "enum": [x.id for x in profile.experiences]}},
            "experience": {"type": "array", "items": {
                "type": "object",
                "properties": {"experience_id": {"type": "string", "enum": [x.id for x in profile.experiences]},
                               "bullets": items},
                "required": ["experience_id", "bullets"], "additionalProperties": False}},
            "education": {"type": "array", "items": {
                "type": "object",
                "properties": {"education_id": {"type": "string", "enum": [e.id for e in profile.education]},
                               "bullets": items},
                "required": ["education_id", "bullets"], "additionalProperties": False}},
            "skills": {"type": "array", "items": {
                "type": "object", "properties": {"group": {"type": "string"}, "items": items},
                "required": ["group", "items"], "additionalProperties": False}},
            "keywords_used": {"type": "array", "items": {"type": "string"}},
            "flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "focus_experience_ids", "experience", "education", "skills",
                     "keywords_used", "flags"],
        "additionalProperties": False,
    }


def letter_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "salutation": {"type": "string"},
            "paragraphs": {"type": "array", "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": [k.value for k in ParagraphKind]},
                               "text": {"type": "string"},
                               "evidence_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["kind", "text", "evidence_ids"], "additionalProperties": False}},
            "sign_off": {"type": "string"},
            "flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subject", "salutation", "paragraphs", "sign_off", "flags"],
        "additionalProperties": False,
    }


# ---------------- evidence gate ----------------

def _gate(item: Item, known: set[str], where: str, notes: list[str], required: bool = True) -> bool:
    valid = [i for i in item.evidence_ids if i in known]
    if len(valid) != len(item.evidence_ids):
        notes.append(f"{where}: removed unknown evidence {sorted(set(item.evidence_ids) - set(valid))}")
    item.evidence_ids = valid
    if required and not valid:
        notes.append(f"{where}: dropped (no valid evidence): '{item.text[:70]}'")
        return False
    return True


def enforce_cv(plan: CVPlan, profile: MasterProfile) -> list[str]:
    """Drop every claim without valid evidence; add jobs/degrees the AI left out (no gaps)."""
    known = {e.id for e in profile.evidence}
    notes: list[str] = []
    if not _gate(plan.summary, known, "summary", notes):
        plan.summary = Item(text="", evidence_ids=[])
    seen_exp, seen_edu = set(), set()
    exp_blocks = []
    for b in plan.experience:
        if b.experience_id in seen_exp:
            continue
        seen_exp.add(b.experience_id)
        b.bullets = [x for x in b.bullets if _gate(x, known, f"experience {b.experience_id}", notes)]
        exp_blocks.append(b)
    for x in profile.experiences:
        if x.id not in seen_exp:
            notes.append(f"experience {x.id} was missing - added without bullets (avoids a gap)")
            exp_blocks.append(ExperienceBlock(experience_id=x.id, bullets=[]))
    # Emphasis (user rule 2026-11-15): relevant roles get the detail, the others are condensed.
    valid_ids = {x.id for x in profile.experiences}
    plan.focus_experience_ids = [i for i in dict.fromkeys(plan.focus_experience_ids) if i in valid_ids]
    if not plan.focus_experience_ids:
        best = max(exp_blocks, key=lambda b: len(b.bullets), default=None)
        if best is not None:
            plan.focus_experience_ids = [best.experience_id]
            notes.append(f"no focus role given - using {best.experience_id} (most bullets)")
    for b in exp_blocks:
        cap = MAX_FOCUS_BULLETS if b.experience_id in plan.focus_experience_ids else MAX_OTHER_BULLETS
        if len(b.bullets) > cap:
            notes.append(f"experience {b.experience_id}: kept the first {cap} of {len(b.bullets)} bullets "
                         f"({'focus' if cap == MAX_FOCUS_BULLETS else 'less relevant'} role)")
            b.bullets = b.bullets[:cap]
    # Order is a fact, not a style choice: reverse-chronological (Austrian / ATS convention).
    start = {x.id: (x.current is True, x.end or "9999-99", x.start or "") for x in profile.experiences}
    plan.experience = sorted(exp_blocks, key=lambda b: start[b.experience_id], reverse=True)
    edu_blocks = []
    for b in plan.education:
        if b.education_id in seen_edu:
            continue
        seen_edu.add(b.education_id)
        b.bullets = [x for x in b.bullets if _gate(x, known, f"education {b.education_id}", notes)]
        edu_blocks.append(b)
    for e in profile.education:
        if e.id not in seen_edu:
            edu_blocks.append(EducationBlock(education_id=e.id, bullets=[]))
    done = {e.id: e.completed or "" for e in profile.education}
    plan.education = sorted(edu_blocks, key=lambda b: done[b.education_id], reverse=True)
    for g in plan.skills:
        g.items = [x for x in g.items if _gate(x, known, f"skills '{g.group}'", notes)]
    plan.skills = [g for g in plan.skills if g.items]
    return notes


def enforce_letter(plan: LetterPlan, profile: MasterProfile) -> list[str]:
    known = {e.id for e in profile.evidence}
    notes: list[str] = []
    kept = []
    for i, p in enumerate(plan.paragraphs):
        item = Item(text=p.text, evidence_ids=p.evidence_ids)
        needs = p.kind == ParagraphKind.EVIDENCE
        if _gate(item, known, f"paragraph {i + 1} ({p.kind})", notes, required=needs):
            p.evidence_ids = item.evidence_ids
            kept.append(p)
    plan.paragraphs = kept
    return notes
