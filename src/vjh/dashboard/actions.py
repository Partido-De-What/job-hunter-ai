"""Quick actions from the dashboard tables (several jobs at once).

They reuse the tracker's own rules (append-only history, reason required for feedback).
Each call is one database transaction: either every job is changed or none."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ..tracker.feedback import add_feedback, dismiss
from ..tracker.service import set_status

GOOD_REASONS = ["Good fit with my experience", "Interesting employer", "Right location", "Good career step"]
BAD_REASONS = ["Wrong field", "Needs more experience than I have", "Too junior for me", "Language level too high",
               "Location / commute", "Salary too low", "Contract type (temporary, part-time)",
               "Not interested in this employer"]


def reason_text(choice: str, extra: str = "") -> str:
    extra = (extra or "").strip()
    return f"{choice}: {extra}" if choice and extra else (choice or extra)


def rate(engine: Engine, job_ids: list[int], good: bool, reason: str, archive: bool = False) -> list[str]:
    """👍 / 👎 for each job. 'Not for me' + archive also moves the application to 'archived'."""
    msgs = []
    with Session(engine) as s, s.begin():
        for jid in job_ids:
            if not good and archive:
                dismiss(s, jid, reason)
                msgs.append(f"#{jid}: not for me, archived")
            else:
                add_feedback(s, jid, good, reason)
                msgs.append(f"#{jid}: {'good match' if good else 'not for me'}")
    return msgs


def change_status(engine: Engine, job_ids: list[int], status: str, when: date | None = None,
                  note: str = "") -> tuple[list[str], list[str]]:
    """Set the same status on several jobs. Returns (messages, warnings)."""
    msgs, warns = [], []
    with Session(engine) as s, s.begin():
        for jid in job_ids:
            ch = set_status(s, jid, status, note=note, when=when)
            extra = f" (follow-up reminder {ch.app.follow_up_date})" if status == "applied" else ""
            msgs.append(f"#{jid}: {ch.from_status} → {ch.to_status}{extra}")
            warns += [f"#{jid}: {w}" for w in ch.warnings]
    return msgs, warns
