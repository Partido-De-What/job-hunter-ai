"""Python-computed scoring (ARCHITECTURE.md §4). The AI never computes the final score."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.common import Recommendation, RequirementKind, RequirementStatus
from ..models.evaluation import AIEvaluation
from ..models.settings import ScoringSettings

SUITABILITY = ("professional_experience", "education", "technical_skills", "responsibility_alignment")
ATTRACTIVENESS = ("location_practical", "career_development")


@dataclass
class ScoreResult:
    overall: float
    category: str
    points: dict[str, float]               # rescaled to the configured weights
    eligibility: str                       # eligible | unclear | potentially_ineligible
    potentially_ineligible: bool
    professional_suitability: float        # % of the suitability dimensions
    career_attractiveness: float           # % of the attractiveness dimensions
    recommendation: Recommendation
    notes: list[str]


def compute(ai: AIEvaluation, scoring: ScoringSettings) -> ScoreResult:
    points: dict[str, float] = {}
    for s in ai.sub_scores:
        weight = scoring.weights[s.dimension]
        points[s.dimension] = round(s.points / s.max_points * weight, 2)
    overall = round(sum(points.values()), 1)

    mandatory = [r for r in ai.requirements if r.kind == RequirementKind.MANDATORY]
    not_met = [r for r in mandatory if r.status == RequirementStatus.NOT_MET]
    unclear = [r for r in mandatory if r.status in (RequirementStatus.UNKNOWN, RequirementStatus.PARTIALLY_MET)]
    if not_met:
        eligibility = "potentially_ineligible"
    elif unclear:
        eligibility = "unclear"
    else:
        eligibility = "eligible"

    def pct(dims):
        top = sum(scoring.weights[d] for d in dims)
        return round(100 * sum(points[d] for d in dims) / top, 1) if top else 0.0

    rec = ai.recommendation
    notes: list[str] = []
    if rec == Recommendation.APPLY and not_met:
        rec = Recommendation.CONSIDER
        notes.append("AI said 'apply' but a mandatory requirement is not met -> 'consider' (check it yourself)")
    elif rec == Recommendation.APPLY and overall < 60:
        rec = Recommendation.CONSIDER
        notes.append(f"AI said 'apply' but the score is {overall} -> 'consider'")

    return ScoreResult(
        overall=overall,
        category=scoring.category_for(overall),
        points=points,
        eligibility=eligibility,
        potentially_ineligible=bool(not_met),
        professional_suitability=pct(SUITABILITY),
        career_attractiveness=pct(ATTRACTIVENESS),
        recommendation=rec,
        notes=notes,
    )


def clean_evidence(raw: dict, known_ids: set[str]) -> tuple[dict, list[str]]:
    """Drop evidence IDs that are not in the profile. A met/partially-met requirement left
    without valid evidence becomes 'unknown' (unknown never counts as met)."""
    notes: list[str] = []
    for req in raw.get("requirements", []):
        ids = req.get("evidence_ids") or []
        valid = [i for i in ids if i in known_ids]
        if len(valid) != len(ids):
            notes.append(f"removed unknown evidence {sorted(set(ids) - set(valid))} from '{req.get('text', '')[:60]}'")
        req["evidence_ids"] = valid
        if req.get("status") in ("met", "partially_met") and not valid:
            req["status"] = "unknown"
            req["reason"] = (req.get("reason") or "") + " [downgraded to unknown: no valid evidence cited]"
            notes.append(f"'{req.get('text', '')[:60]}' downgraded to unknown (no valid evidence)")
    return raw, notes
