"""Evaluator backend that uses the Claude Code CLI (`claude -p`) and the user's Claude
subscription instead of a paid API key.

Safety: no tools ("--tools ''"), no MCP servers, no slash commands, a replaced system prompt,
no saved session, and an empty temporary working folder (no project files, no CLAUDE.md).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..models.settings import LLMSettings
from .client import LLMResult


class BackendUnavailable(Exception):
    """The backend cannot serve any more requests right now (not logged in, usage limit,
    CLI missing). The evaluation run stops instead of failing every remaining job."""


_STOP_WORDS = ("not logged in", "please run /login", "usage limit", "rate limit", "limit reached",
               "credit balance", "authentication", "invalid api key", "oauth")


def find_cli(settings: LLMSettings) -> str | None:
    return shutil.which(settings.claude_cli)


class ClaudeCodeLLM:
    def __init__(self, settings: LLMSettings, runner=subprocess.run):
        self.settings = settings
        self._run = runner
        self.cli = find_cli(settings) if runner is subprocess.run else settings.claude_cli
        if self.cli is None:
            raise BackendUnavailable(f"Claude Code CLI '{settings.claude_cli}' not found. Install Claude Code "
                                     "or set llm.backend: api in config/settings.yaml.")
        self.workdir = Path(tempfile.mkdtemp(prefix="vjh-eval-"))
        # Never let an API key override the subscription login.
        self.env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}

    def complete_json(self, *, system: str, user: str, schema: dict, model: str) -> LLMResult:
        prompt_file = self.workdir / "system.txt"
        prompt_file.write_text(system, encoding="utf-8")
        cmd = [self.cli, "-p", "--output-format", "json",
               "--json-schema", json.dumps(schema),
               "--model", model, "--effort", self.settings.effort,
               "--system-prompt-file", str(prompt_file),
               "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
               "--no-session-persistence"]
        extra = {}
        if os.name == "nt" and self._run is subprocess.run:
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW   # no console flash in the daily task
        try:
            proc = self._run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                             env=self.env, cwd=str(self.workdir), timeout=self.settings.cli_timeout_seconds,
                             **extra)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Claude Code did not answer within {self.settings.cli_timeout_seconds}s") from exc
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            msg = (proc.stderr or proc.stdout or "").strip()[:300]
            if any(w in msg.lower() for w in _STOP_WORDS):
                raise BackendUnavailable(msg) from exc
            raise RuntimeError(f"Claude Code returned no JSON (exit {proc.returncode}): {msg}") from exc

        message = str(out.get("result") or "")
        if out.get("is_error"):
            if any(w in message.lower() for w in _STOP_WORDS) or out.get("terminal_reason") == "api_error":
                raise BackendUnavailable(message[:300] or "Claude Code reported an error")
            raise RuntimeError(f"Claude Code error: {message[:300]}")

        structured = out.get("structured_output")
        text = json.dumps(structured) if structured is not None else message
        usage = out.get("usage") or {}
        served = next(iter(out.get("modelUsage") or {}), model)
        return LLMResult(
            text=text,
            stop_reason=out.get("stop_reason") or "end_turn",
            model=served,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        )

    def login_status(self) -> dict:
        proc = self._run([self.cli, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                         env=self.env, timeout=60)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"loggedIn": False, "raw": (proc.stdout or proc.stderr)[:200]}
