"""AI evaluation output schema.

The AI classifies requirements and proposes sub-scores with reasons. It never
computes the final score: Python does that (Phase 3) from these fields.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Recommendation, RequirementKind, RequirementStatus

# Dimension -> maximum points (ARCHITECTURE.md §4). Kept in sync with settings.yaml
# by a test.
DIMENSIONS = (
    "professional_experience",
    "education",
    "technical_skills",
    "responsibility_alignment",
    "language_compatibility",
    "location_practical",
    "career_development",
)


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    quote: str = ""                       # verbatim text from the ad
    kind: RequirementKind
    status: RequirementStatus
    reason: str = ""
    evidence_ids: list[str] = []          # profile evidence supporting met/partial

    @model_validator(mode="after")
    def met_needs_evidence(self) -> Requirement:
        if self.status in (RequirementStatus.MET, RequirementStatus.PARTIALLY_MET) and not self.evidence_ids:
            raise ValueError(f"Requirement '{self.text}' is {self.status} but cites no evidence")
        return self


class SubScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    points: float = Field(ge=0)
    max_points: float = Field(gt=0)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def within_max(self) -> SubScore:
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"Unknown scoring dimension: {self.dimension}")
        if self.points > self.max_points:
            raise ValueError(f"{self.dimension}: {self.points} > max {self.max_points}")
        return self


class AIEvaluation(BaseModel):
    """What the model returns. Validated strictly; invalid output is discarded."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement]
    sub_scores: list[SubScore]
    best_cv_profile: str = Field(pattern=r"^[A-Z]$")   # a letter from settings: candidate.cv_strategies
    best_matching_experience: list[str] = []
    technical_skill_matches: list[str] = []
    missing_skills: list[str] = []
    language_concerns: list[str] = []
    career_development: str = ""
    potential_obstacles: list[str] = []
    recommendation: Recommendation
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def one_score_per_dimension(self) -> AIEvaluation:
        dims = [s.dimension for s in self.sub_scores]
        if sorted(dims) != sorted(DIMENSIONS):
            raise ValueError(f"sub_scores must cover each dimension exactly once, got {dims}")
        return self


class StoredEvaluation(BaseModel):
    """An AI evaluation plus the Python-computed results and cache key."""

    job_id: int
    ai: AIEvaluation
    overall_score: float = Field(ge=0, le=100)
    category: str
    potentially_ineligible: bool
    model: str
    profile_version: int
    content_hash: str
    evaluated_at: datetime
