"""Safe edits of the user's own files, used by the setup wizard and the Settings page.

- YAML is edited round-trip with ruamel.yaml, so the explanations (comments) in
  config/settings.yaml survive. Every change is validated before the file is replaced.
- .env: only the given keys change, other lines stay as they are. Values are never printed.
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _ryaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 110
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _flow(value: Any) -> Any:
    """Short lists and small mappings of plain values are written on one line, like the originals."""
    if isinstance(value, list) and all(not isinstance(v, (list, dict)) for v in value):
        seq = CommentedSeq(value)
        seq.fa.set_flow_style()
        return seq
    if isinstance(value, dict) and all(not isinstance(v, (list, dict)) for v in value.values()):
        m = CommentedMap(value)
        m.fa.set_flow_style()
        return m
    if isinstance(value, dict):
        return CommentedMap({k: _flow(v) for k, v in value.items()})
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def update_yaml(path: Path, changes: dict[str, Any], validate: Callable[[dict], Any] | None = None) -> None:
    """Set values by dotted key ('automation.daily_time': '08:00'), keep comments, validate, write."""
    y = _ryaml()
    doc = y.load(path.read_text(encoding="utf-8")) if path.exists() else CommentedMap()
    for dotted, value in changes.items():
        node = doc
        *parents, last = dotted.split(".")
        for key in parents:
            if key not in node or node[key] is None:
                node[key] = CommentedMap()
            node = node[key]
        node[last] = _flow(value)
    buf = io.StringIO()
    y.dump(doc, buf)
    text = buf.getvalue()
    data = yaml.safe_load(text)
    if validate:
        validate(data)                    # raises (e.g. pydantic ValidationError): nothing is written
    _atomic_write(path, text)


def replace_yaml_text(path: Path, text: str, validate: Callable[[dict], Any] | None = None) -> None:
    """Replace a whole YAML file with user-edited text, after checking that it parses and validates."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("the text must be a YAML mapping (key: value)")
    if validate:
        validate(data)
    _atomic_write(path, text if text.endswith("\n") else text + "\n")


# ---------------- .env ----------------

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
SECRET_KEYS = {"SMTP_PASSWORD", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw.split(" #", 1)[0].strip()


def _quote(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line)
            if m:
                out[m.group(1)] = _unquote(m.group(2))
    return out


def env_status(path: Path) -> dict[str, Any]:
    """What the forms may show: plain values for non-secret keys, only True/False for secrets."""
    env = read_env(path)
    return {k: (bool(v) if k in SECRET_KEYS else v) for k, v in env.items()}


def update_env(path: Path, values: dict[str, str | None], apply_to_process: bool = True) -> None:
    """Set (or with None remove) keys in .env; other lines and comments are kept.
    An empty string for a secret means 'keep the current one'."""
    values = {k: v for k, v in values.items() if not (k in SECRET_KEYS and v == "")}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out = []
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) in values:
            key = m.group(1)
            if values[key] is not None and key not in done:
                out.append(f"{key}={_quote(values[key])}")
            done.add(key)
            continue
        out.append(line)
    for key, value in values.items():
        if key not in done and value is not None:
            out.append(f"{key}={_quote(value)}")
    _atomic_write(path, "\n".join(out) + "\n")
    if apply_to_process:                  # the running dashboard / process sees the change at once
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
