"""Configuration loads without secrets and contains none."""

import re

import pytest
import yaml
from conftest import PROJECT_ROOT
from pydantic import ValidationError

from vjh.config import load_config, load_home_location
from vjh.models.settings import Settings


def test_settings_load_without_any_secrets(fake_project):
    cfg = load_config()
    assert cfg.settings.preferences.min_gross_annual_salary == 35000
    assert cfg.settings.preferences.max_commute_minutes_one_way == 60
    assert cfg.settings.candidate.country and cfg.settings.candidate.cv_strategies
    assert cfg.settings.collection.allow_jobspy is True           # JobSpy is on by default


def test_weights_match_architecture():
    cfg = load_config(PROJECT_ROOT)
    assert cfg.settings.scoring.weights == {
        "professional_experience": 25, "education": 20, "technical_skills": 20,
        "responsibility_alignment": 15, "language_compatibility": 10,
        "location_practical": 5, "career_development": 5,
    }


@pytest.mark.parametrize("score,expected", [(95, "excellent"), (90, "excellent"), (85, "strong"),
                                            (70, "good_potential"), (60, "possible"), (59.9, "low_priority")])
def test_categories(score, expected):
    assert load_config(PROJECT_ROOT).settings.scoring.category_for(score) == expected


def test_weights_must_sum_to_100():
    data = yaml.safe_load((PROJECT_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    data["scoring"]["weights"]["education"] = 30
    with pytest.raises(ValidationError, match="sum to 100"):
        Settings.model_validate(data)


def test_settings_file_contains_no_secrets():
    """Check every key and value (comments ignored) for secret-looking content."""
    data = yaml.safe_load((PROJECT_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    flat: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                flat.append(str(k))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        else:
            flat.append(str(node))

    walk(data)
    words = [w.lower() for w in flat]
    for w in words:
        assert not w.startswith("sk-ant"), "settings.yaml contains an API key"
        assert w not in {"api_key", "apikey", "password", "secret", "token", "auth_token", "smtp_password"}, w


def test_settings_db_path_is_outside_project_by_default():
    assert "%LOCALAPPDATA%" in load_config(PROJECT_ROOT).settings.database.path


def test_home_address_not_in_code_or_config():
    """If a real home location exists, its street must not appear in committed files."""
    home_file = PROJECT_ROOT / "personal" / "home_location.yaml"
    if not home_file.exists():
        pytest.skip("no personal/home_location.yaml on this machine")
    street = load_home_location(home_file).street
    if not street:
        pytest.skip("no street set")
    pattern = re.compile(re.escape(street), re.IGNORECASE)
    for folder in ("src", "config", "tests", "docs"):
        for path in (PROJECT_ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".md", ".toml", ".txt"}:
                assert not pattern.search(path.read_text(encoding="utf-8", errors="ignore")), path
