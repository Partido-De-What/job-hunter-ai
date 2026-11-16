"""`vjh init` and `vjh status` against a throw-away project."""

import hashlib

from conftest import PROJECT_ROOT

from vjh.cli import main
from vjh.config import load_profile


def _digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def test_status_before_init_reports_missing_db(fake_project, capsys):
    assert main(["status"]) == 1
    assert "run: vjh init" in capsys.readouterr().out


def test_init_then_status_ok(fake_project, capsys):
    personal_before = _digest(fake_project / "personal")
    assert main(["init"]) == 0
    assert main(["init"]) == 0                      # idempotent
    assert _digest(fake_project / "personal") == personal_before   # never modified
    assert (fake_project / "data" / "logs").is_dir()
    capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "evidence                   4 rows" in out
    assert "Testcity" not in out                     # home location never printed
    assert "Overall: OK" in out


def test_status_flags_unprotected_gitignore(fake_project, capsys):
    main(["init"])
    (fake_project / ".gitignore").write_text("data/\n", encoding="utf-8")
    assert main(["status"]) == 1
    assert "does not protect" in capsys.readouterr().out


def test_real_profile_is_valid_if_present():
    """Validates personal/master_profile.yaml on this machine (skipped elsewhere)."""
    path = PROJECT_ROOT / "personal" / "master_profile.yaml"
    if not path.exists():
        import pytest
        pytest.skip("no real profile on this machine")
    profile = load_profile(path)
    assert profile.personal.full_name and profile.languages
