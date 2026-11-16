"""Email the daily report to the user (and only the user).

Settings come from .env: SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = SSL), SMTP_USER,
SMTP_PASSWORD, NOTIFY_TO (default: SMTP_USER). Unencrypted SMTP is refused.
No attachments: CVs and other personal documents are never emailed.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from .report import DailyReport, render_html

ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class NotifyError(Exception):
    pass


@dataclass
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    to: str

    def __repr__(self) -> str:                      # never show the password
        return f"MailConfig(host={self.host!r}, port={self.port}, user={self.user!r}, to={self.to!r})"


def mail_config(env: dict | None = None) -> MailConfig | None:
    """None when email is not set up. Raises NotifyError when it is set up wrongly."""
    env = env if env is not None else os.environ
    host, user, password = env.get("SMTP_HOST", ""), env.get("SMTP_USER", ""), env.get("SMTP_PASSWORD", "")
    if not password:
        return None          # not set up yet (e.g. app password still to be added): skip, not an error
    missing = [k for k, v in (("SMTP_HOST", host), ("SMTP_USER", user), ("SMTP_PASSWORD", password)) if not v]
    if missing:
        raise NotifyError(f"email is half set up in .env - missing: {', '.join(missing)}")
    to = (env.get("NOTIFY_TO") or user).strip()
    if not ADDRESS.match(to):
        raise NotifyError("NOTIFY_TO must be exactly one email address (your own)")
    try:
        port = int(env.get("SMTP_PORT") or 587)
    except ValueError as exc:
        raise NotifyError("SMTP_PORT must be a number (587 or 465)") from exc
    if port not in (465, 587):
        raise NotifyError("SMTP_PORT must be 587 (STARTTLS) or 465 (SSL): unencrypted email is not allowed")
    return MailConfig(host=host, port=port, user=user, password=password, to=to)


def text_summary(rep: DailyReport) -> str:
    lines = [rep.subject, ""]
    for e in rep.top_matches[:10]:
        flag = "  (! a mandatory requirement is not met)" if e["ineligible"] else ""
        lines.append(f"- {e['score']:.0f}  #{e['id']} {e['title']} | {e['employer']}{flag}")
    if not rep.top_matches:
        lines.append("No new jobs above the recommendation threshold.")
    if rep.documents:
        lines.append(f"\n{len(rep.documents)} document(s) created - review them in the dashboard.")
    for d in rep.due:
        lines.append(f"Due {d['when']}: {d['kind']} - #{d['job']} {d['title']}")
    for e in rep.run_errors:
        lines.append(f"Error: {e}")
    lines.append("\nOpen the dashboard with: vjh dashboard")
    return "\n".join(lines)


def build_message(rep: DailyReport, cfg: MailConfig) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = rep.subject
    msg["From"] = cfg.user
    msg["To"] = cfg.to
    msg.set_content(text_summary(rep))
    msg.add_alternative(render_html(rep), subtype="html")
    return msg


def send(msg: EmailMessage, cfg: MailConfig, smtp=smtplib) -> None:
    context = ssl.create_default_context()
    try:
        if cfg.port == 465:
            with smtp.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=30) as s:
                s.login(cfg.user, cfg.password)
                s.send_message(msg, to_addrs=[cfg.to])
        else:
            with smtp.SMTP(cfg.host, cfg.port, timeout=30) as s:
                s.starttls(context=context)
                s.login(cfg.user, cfg.password)
                s.send_message(msg, to_addrs=[cfg.to])
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"sending failed: {type(exc).__name__}: {exc}") from exc
