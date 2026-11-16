"""Master profile: the single source of truth for every generated document.

Every claim lives in the evidence registry with an ID. Experiences, education and
skills reference evidence IDs; documents (Phase 4) may only use claims that resolve
to an evidence item.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import CEFRLevel, Confidence, EvidenceSource, VerificationStatus

# Language levels in the profile are the truth: documents and evaluations may never claim
# a higher level than stated here (enforced by the prompts and by the quality check).

YEAR_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
EVIDENCE_ID = r"^EV-[A-Z0-9]+-\d{3}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceItem(_Strict):
    id: str = Field(pattern=EVIDENCE_ID)
    claim: str = Field(min_length=3)
    supporting_experience: str  # id of an experience/education entry, or "languages", "general"
    source: EvidenceSource
    source_detail: str = ""      # e.g. "CV p.1, first job, bullet 1"
    confidence: Confidence
    verification_status: VerificationStatus
    notes: str = ""


class TodoItem(_Strict):
    id: str = Field(pattern=r"^TODO-\d{3}$")
    field: str
    question: str
    cv_says: str = ""
    spec_says: str = ""


class PersonalDetails(_Strict):
    """Contact block for the CV header (what is shown is set in settings: candidate.cv_conventions).
    The home address is NOT stored here; it lives only in personal/home_location.yaml."""

    full_name: str
    email: str | None = None
    phone: str | None = None
    nationalities: list[str] = []
    right_to_work: bool | None = None          # in the target country (settings: candidate.country)
    right_to_work_note: str | None = None      # shown on the CV, e.g. "EU citizen - no permit needed"
    date_of_birth: str | None = None
    photo_path: str | None = None


class Experience(_Strict):
    id: str
    employer: str
    title: str
    location: str | None = None
    employment_type: str | None = None        # e.g. "interim contract (week by week)"
    start: str | None = Field(default=None, pattern=YEAR_MONTH)
    end: str | None = Field(default=None, pattern=YEAR_MONTH)  # None = current or unknown
    current: bool | None = None
    priority_profiles: list[str] = []         # CV strategy letters (settings: candidate.cv_strategies)
    evidence_ids: list[str] = []
    # PRIVATE (e.g. past salary, exact contract details): for the user's own reference only.
    # Never sent to the AI, never printed in CVs or letters (enforced by tests).
    private_notes: list[str] = []


class Education(_Strict):
    id: str
    degree: str
    field: str
    institution: str
    completed: str | None = Field(default=None, pattern=YEAR_MONTH)
    evidence_ids: list[str] = []


class LanguageSkill(_Strict):
    language: str
    level: CEFRLevel
    certified: bool = False
    certificate: str | None = None            # e.g. "Cambridge English: C2 Proficiency"
    note: str | None = None                   # shown after the level, e.g. "actively learning"
    evidence_ids: list[str] = []

    @model_validator(mode="after")
    def certificate_implies_certified(self) -> LanguageSkill:
        if self.certificate and not self.certified:
            raise ValueError(f"{self.language}: certificate given but certified is false")
        return self


class MasterProfile(_Strict):
    profile_version: int = Field(ge=1)
    personal: PersonalDetails
    experiences: list[Experience]
    education: list[Education]
    languages: list[LanguageSkill]
    skills: list[str] = []          # evidence IDs for standalone skills
    evidence: list[EvidenceItem]
    todos: list[TodoItem] = []

    @model_validator(mode="after")
    def evidence_references_resolve(self) -> MasterProfile:
        ids = [e.id for e in self.evidence]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate evidence IDs: {sorted(dupes)}")
        known = set(ids)
        refs: list[str] = list(self.skills)
        for group in (self.experiences, self.education, self.languages):
            for item in group:
                refs.extend(item.evidence_ids)
        missing = sorted({r for r in refs if r not in known})
        if missing:
            raise ValueError(f"Unknown evidence IDs referenced: {missing}")
        return self

    def evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {e.id: e for e in self.evidence}


class HomeLocation(_Strict):
    """Private. Loaded only from personal/home_location.yaml."""

    street: str | None = None
    postcode: str | None = None
    city: str
    country: str = "Austria"
    latitude: float | None = None
    longitude: float | None = None
