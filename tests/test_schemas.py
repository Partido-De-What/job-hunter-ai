"""Schema validation."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from vjh.models import AIEvaluation, Job, MasterProfile, Requirement, Salary, SubScore
from vjh.models.evaluation import DIMENSIONS

# ---------- master profile ----------

def test_fake_profile_is_valid(profile_data):
    profile = MasterProfile.model_validate(profile_data)
    assert profile.evidence_by_id()["EV-LAB-001"].claim == "Ran PCR assays"


def test_example_profile_is_valid():
    """The fictional example shipped in examples/ must always load."""
    import yaml
    from conftest import PROJECT_ROOT
    data = yaml.safe_load((PROJECT_ROOT / "examples" / "master_profile.example.yaml").read_text(encoding="utf-8"))
    profile = MasterProfile.model_validate(data)
    assert profile.personal.full_name == "Alex Example" and profile.languages[2].note == "actively learning"


def test_certificate_requires_certified_flag(profile_data):
    profile_data["languages"][0]["certificate"] = "Some exam"
    with pytest.raises(ValidationError, match="certified is false"):
        MasterProfile.model_validate(profile_data)
    profile_data["languages"][0]["certified"] = True
    assert MasterProfile.model_validate(profile_data).languages[0].certificate == "Some exam"


def test_unknown_evidence_reference_is_rejected(profile_data):
    profile_data["experiences"][0]["evidence_ids"].append("EV-LAB-999")
    with pytest.raises(ValidationError, match="EV-LAB-999"):
        MasterProfile.model_validate(profile_data)


def test_duplicate_evidence_id_is_rejected(profile_data):
    profile_data["evidence"].append(dict(profile_data["evidence"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        MasterProfile.model_validate(profile_data)


def test_profile_has_no_address_field(profile_data):
    profile_data["personal"]["address"] = "Somewhere 1"
    with pytest.raises(ValidationError):
        MasterProfile.model_validate(profile_data)


def test_bad_date_format_is_rejected(profile_data):
    profile_data["experiences"][0]["start"] = "June 2023"
    with pytest.raises(ValidationError):
        MasterProfile.model_validate(profile_data)


# ---------- salary / job ----------

def test_monthly_salary_never_assumes_14_payments():
    s = Salary(amount_min=2800, period="monthly")
    assert s.payments_per_year is None
    assert s.annual_gross_min() is None


def test_monthly_salary_annualised_only_when_payments_stated():
    assert Salary(amount_min=2800, period="monthly", payments_per_year=14).annual_gross_min() == 39200


def test_salary_range_must_be_ordered():
    with pytest.raises(ValidationError):
        Salary(amount_min=50000, amount_max=40000, period="annual")


def test_salary_amount_needs_period():
    with pytest.raises(ValidationError):
        Salary(amount_min=40000)


def _job(**kw):
    now = datetime(2026, 11, 15, 8, 0)
    base = dict(title="Lab Technician", employer="Example GmbH", source="manual",
                discovered_at=now, retrieved_at=now, description="Run PCR.")
    return Job(**{**base, **kw})


def test_job_hash_ignores_whitespace_and_case():
    assert _job().content_hash == _job(description="  run   PCR. ").content_hash
    assert _job().content_hash != _job(description="Run qPCR.").content_hash


# ---------- AI evaluation ----------

def _scores(**override):
    maxes = dict(zip(DIMENSIONS, [25, 20, 20, 15, 10, 5, 5], strict=True))
    return [SubScore(dimension=d, points=override.get(d, 1), max_points=m, reason="x")
            for d, m in maxes.items()]


def test_met_requirement_needs_evidence():
    with pytest.raises(ValidationError, match="evidence"):
        Requirement(text="PCR", kind="mandatory", status="met")


def test_unknown_requirement_needs_no_evidence():
    assert Requirement(text="Driving licence", kind="mandatory", status="unknown")


def test_subscore_cannot_exceed_max():
    with pytest.raises(ValidationError):
        SubScore(dimension="education", points=21, max_points=20, reason="x")


def test_evaluation_requires_every_dimension_once():
    ok = AIEvaluation(requirements=[], sub_scores=_scores(), best_cv_profile="A",
                      recommendation="consider", explanation="ok")
    assert len(ok.sub_scores) == 7
    with pytest.raises(ValidationError):
        AIEvaluation(requirements=[], sub_scores=_scores()[:-1], best_cv_profile="A",
                     recommendation="consider", explanation="ok")


def test_evaluation_rejects_extra_fields():
    """The model must not be able to smuggle in a final score."""
    with pytest.raises(ValidationError):
        AIEvaluation(requirements=[], sub_scores=_scores(), best_cv_profile="A",
                     recommendation="apply", explanation="ok", overall_score=99)
