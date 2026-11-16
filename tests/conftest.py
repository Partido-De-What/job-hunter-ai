import shutil
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
# The worked-example configuration, frozen for tests, so tests pass whatever a user puts in config/.
EXAMPLE_ROOT = FIXTURES / "example_project"
SECRET_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
               "SMTP_PASSWORD", "NOTIFY_TO")


@pytest.fixture(autouse=True)
def no_real_secrets(monkeypatch):
    """Every test: no real API keys or mail settings, and the real .env is never read.
    A test can therefore never send a real email or use a real account by mistake."""
    import subprocess

    import vjh.config
    import vjh.llm.claude_code
    import vjh.llm.client
    monkeypatch.setattr(vjh.config, "load_dotenv", lambda *a, **k: False)
    for var in SECRET_VARS:
        monkeypatch.delenv(var, raising=False)

    # Guard: tests must never reach the real Claude (subscription or API).
    real_cli_call = vjh.llm.claude_code.ClaudeCodeLLM.complete_json

    def guarded_cli(self, **kw):
        if self._run is subprocess.run:
            raise RuntimeError("TEST GUARD: a test tried to call the real Claude Code CLI")
        return real_cli_call(self, **kw)

    def guarded_api(self, **kw):
        raise RuntimeError("TEST GUARD: a test tried to call the real Anthropic API")

    monkeypatch.setattr(vjh.llm.claude_code.ClaudeCodeLLM, "complete_json", guarded_cli)
    monkeypatch.setattr(vjh.llm.client.AnthropicLLM, "complete_json", guarded_api)


@pytest.fixture
def profile_data() -> dict:
    with open(FIXTURES / "profile_minimal.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def fake_project(tmp_path, monkeypatch) -> Path:
    """A throw-away project folder with the example settings and a FAKE profile."""
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "personal").mkdir()
    for name in ("settings.yaml", "sources.yaml", "search_terms.yaml"):
        shutil.copy(EXAMPLE_ROOT / "config" / name, root / "config" / name)
    shutil.copy(PROJECT_ROOT / ".gitignore", root / ".gitignore")
    shutil.copy(FIXTURES / "profile_minimal.yaml", root / "personal" / "master_profile.yaml")
    (root / "personal" / "home_location.yaml").write_text("city: Testcity\n", encoding="utf-8")
    monkeypatch.setenv("VJH_HOME", str(root))
    monkeypatch.setenv("VJH_DB_PATH", str(tmp_path / "fake.db"))
    for var in ("ANTHROPIC_API_KEY", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    return root
