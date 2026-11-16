"""JSON schema for structured outputs. Mirrors models.evaluation.AIEvaluation, which
re-validates every answer (including the numeric limits JSON schema does not enforce here)."""

from ..models.evaluation import DIMENSIONS

_STR_LIST = {"type": "array", "items": {"type": "string"}}

def evaluation_schema(cv_strategies: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "quote": {"type": "string"},
                        "kind": {"type": "string", "enum": ["mandatory", "desirable"]},
                        "status": {"type": "string", "enum": ["met", "partially_met", "not_met", "unknown"]},
                        "reason": {"type": "string"},
                        "evidence_ids": _STR_LIST,
                    },
                    "required": ["text", "quote", "kind", "status", "reason", "evidence_ids"],
                    "additionalProperties": False,
                },
            },
            "sub_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {"type": "string", "enum": list(DIMENSIONS)},
                        "points": {"type": "number"},
                        "max_points": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["dimension", "points", "max_points", "reason"],
                    "additionalProperties": False,
                },
            },
            "best_cv_profile": {"type": "string", "enum": sorted(cv_strategies)},
            "best_matching_experience": _STR_LIST,
            "technical_skill_matches": _STR_LIST,
            "missing_skills": _STR_LIST,
            "language_concerns": _STR_LIST,
            "career_development": {"type": "string"},
            "potential_obstacles": _STR_LIST,
            "recommendation": {"type": "string", "enum": ["apply", "consider", "skip"]},
            "explanation": {"type": "string"},
        },
        "required": ["requirements", "sub_scores", "best_cv_profile", "best_matching_experience",
                     "technical_skill_matches", "missing_skills", "language_concerns",
                     "career_development", "potential_obstacles", "recommendation", "explanation"],
        "additionalProperties": False,
    }
