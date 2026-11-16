"""Housekeeping: remove old run logs and surplus database backups.

Only these two kinds of files are ever deleted:
  data/logs/*.log older than automation.log_retention_days
  <database folder>/vjh.db.bak-*  beyond the newest automation.db_backups_keep
Jobs, documents, reports, the database itself and personal files are never touched.
"""

from __future__ import annotations

import time
from pathlib import Path


def old_logs(log_dir: Path, days: int, now: float | None = None) -> list[Path]:
    if not log_dir.is_dir():
        return []
    limit = (now or time.time()) - days * 86400
    return sorted(p for p in log_dir.glob("*.log") if p.is_file() and p.stat().st_mtime < limit)


def surplus_backups(db_path: Path, keep: int) -> list[Path]:
    backups = sorted((p for p in db_path.parent.glob(f"{db_path.name}.bak-*") if p.is_file()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return backups[keep:]


def clean(log_dir: Path, db_path: Path, *, log_days: int, backups_keep: int) -> tuple[int, int]:
    """Returns (logs removed, backups removed)."""
    logs = old_logs(log_dir, log_days)
    backups = surplus_backups(db_path, backups_keep)
    for p in logs + backups:
        p.unlink(missing_ok=True)
    return len(logs), len(backups)
