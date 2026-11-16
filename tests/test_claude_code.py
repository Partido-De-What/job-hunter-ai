"""Subscription backend (Claude Code CLI) with a simulated `claude` program."""

import json
import subprocess
from datetime import datetime

import pytest
from conftest import EXAMPLE_ROOT
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_phase3 import good_answer, job

from vjh.config import load_config
from vjh.db import migrate
from vjh.db.tables import ApiUsageRow, JobRow
from vjh.llm.claude_code import BackendUnavailable, ClaudeCodeLLM
from vjh.models import MasterProfile
from vjh.pipeline.evaluate import evaluate_jobs

NOW = datetime(2026, 11, 15, 9, 0)
SETTINGS = load_config(EXAMPLE_ROOT).settings.model_copy(deep=True)
SETTINGS.llm.backend = "claude_code"


class FakeCLI:
    """Stands in for subprocess.run(['claude', '-p', ...])."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        r = self.replies.pop(0)
        stdout = r if isinstance(r, str) else json.dumps(r)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")


def ok_reply(answer=None):
    return {"type": "result", "is_error": False, "result": "", "stop_reason": "end_turn",
            "structured_output": answer or good_answer(),
            "usage": {"input_tokens": 100, "output_tokens": 900, "cache_read_input_tokens": 4000},
            "modelUsage": {"claude-opus-5-5": {}}}


NOT_LOGGED_IN = {"type": "result", "is_error": True, "result": "Not logged in · Please run /login",
                 "terminal_reason": "api_error"}


def test_command_is_locked_down_and_uses_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    cli = FakeCLI([ok_reply()])
    llm = ClaudeCodeLLM(SETTINGS.llm, runner=cli)
    res = llm.complete_json(system="SYS", user="JOB", schema={"type": "object"}, model="claude-opus-5-5")
    cmd, kw = cli.calls[0]
    assert cmd[cmd.index("--tools") + 1] == ""                  # no tools at all
    assert "--bare" not in cmd                                  # --bare would ignore the subscription login
    for flag in ("-p", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "ANTHROPIC_API_KEY" not in kw["env"]                 # subscription, never the paid key
    assert kw["input"] == "JOB" and "vjh-eval-" in kw["cwd"]    # empty temp folder, no project files
    assert json.loads(res.text)["recommendation"] == "apply"
    assert res.cache_read_tokens == 4000


def test_result_text_used_when_no_structured_output():
    reply = ok_reply()
    reply["result"] = json.dumps(reply.pop("structured_output"))
    llm = ClaudeCodeLLM(SETTINGS.llm, runner=FakeCLI([reply]))
    assert json.loads(llm.complete_json(system="s", user="u", schema={}, model="m").text)["explanation"]


def test_not_logged_in_raises_backend_unavailable():
    llm = ClaudeCodeLLM(SETTINGS.llm, runner=FakeCLI([NOT_LOGGED_IN]))
    with pytest.raises(BackendUnavailable, match="Not logged in"):
        llm.complete_json(system="s", user="u", schema={}, model="m")


@pytest.fixture
def db(db_path, profile_data):
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as s, s.begin():
        for h in "abc":
            s.add(job(status="relevant", content_hash=h, title=f"Job {h}"))
    yield engine, MasterProfile.model_validate(profile_data)
    engine.dispose()


def test_evaluation_via_subscription_costs_nothing(db):
    engine, profile = db
    llm = ClaudeCodeLLM(SETTINGS.llm, runner=FakeCLI([ok_reply()] * 3))
    res = evaluate_jobs(engine, SETTINGS, profile, llm, now=NOW)
    assert res.evaluated == 3 and res.cost_usd == 0
    with Session(engine) as s:
        rows = s.scalars(select(ApiUsageRow)).all()
        assert {r.purpose for r in rows} == {"evaluate_subscription"}
        assert all(r.estimated_cost_usd == 0 for r in rows)


def test_not_logged_in_stops_run_without_marking_failures(db):
    engine, profile = db
    cli = FakeCLI([NOT_LOGGED_IN])
    res = evaluate_jobs(engine, SETTINGS, profile, ClaudeCodeLLM(SETTINGS.llm, runner=cli), now=NOW)
    assert len(cli.calls) == 1 and res.failed == 0 and "Not logged in" in res.stopped_reason
    with Session(engine) as s:
        assert {j.status for j in s.scalars(select(JobRow))} == {"relevant"}
        assert s.scalar(select(ApiUsageRow)) is None             # does not count as a job failure


def test_daily_job_limit_for_subscription(db):
    engine, profile = db
    limited = SETTINGS.model_copy(deep=True)
    limited.llm.subscription_daily_max_jobs = 2
    cli = FakeCLI([ok_reply()] * 3)
    res = evaluate_jobs(engine, limited, profile, ClaudeCodeLLM(limited.llm, runner=cli), now=NOW)
    assert res.evaluated == 2 and res.stopped_by_cap and len(cli.calls) == 2
