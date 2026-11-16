"""Daily HTML report (Jinja2). All job data is untrusted: autoescaping is always on."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..dashboard.data import salary_text, sources_frame
from ..db.tables import ApiUsageRow, GeneratedDocumentRow, JobEvaluationRow, JobRow, RunRow
from ..tracker.service import due_items


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DailyReport:
    day: date
    since: datetime
    new_by_source: dict[str, int] = field(default_factory=dict)
    evaluated: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    due: list[dict] = field(default_factory=list)
    source_problems: list[dict] = field(default_factory=list)
    run_errors: list[str] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    recommend_min_score: float = 70
    subject_prefix: str = ""

    @property
    def new_total(self) -> int:
        return sum(self.new_by_source.values())

    @property
    def top_matches(self) -> list[dict]:
        return [e for e in self.evaluated if e["score"] >= self.recommend_min_score or e["recommendation"] == "apply"]

    @property
    def has_news(self) -> bool:
        """Worth an email: new good matches, new documents, something due, or a problem."""
        return bool(self.top_matches or self.documents or self.due or self.run_errors
                    or any(not s.ok for s in self.steps) or self.source_problems)

    @property
    def subject(self) -> str:
        parts = [f"{len(self.top_matches)} good match(es)" if self.top_matches else "no new good matches"]
        if self.due:
            parts.append(f"{len(self.due)} due")
        if self.run_errors or any(not s.ok for s in self.steps):
            parts.append("errors")
        return f"{self.subject_prefix}Job search {self.day:%d.%m.%Y}: " + ", ".join(parts)


def build_daily_report(session: Session, now: datetime, settings, steps: list[StepResult] | None = None) -> DailyReport:
    days = settings.automation.report_days_back
    since = datetime.combine(now.date() - timedelta(days=days - 1), datetime.min.time())
    rep = DailyReport(day=now.date(), since=since, steps=steps or [],
                      recommend_min_score=settings.thresholds.recommend_min_score)
    rep.new_by_source = dict(session.execute(
        select(JobRow.source, func.count()).where(JobRow.discovered_at >= since).group_by(JobRow.source)).all())
    seen: set[int] = set()
    for ev in session.scalars(select(JobEvaluationRow).where(JobEvaluationRow.evaluated_at >= since)
                              .order_by(JobEvaluationRow.overall_score.desc())):
        if ev.job_id in seen:
            continue
        seen.add(ev.job_id)
        j = session.get(JobRow, ev.job_id)
        if j is None or j.status == "duplicate":
            continue
        rep.evaluated.append({"id": j.id, "score": ev.overall_score, "category": ev.category.replace("_", " "),
                              "recommendation": ev.recommendation, "ineligible": ev.potentially_ineligible,
                              "title": j.title, "employer": j.employer, "location": j.location or "",
                              "salary": salary_text(j.salary), "deadline": j.deadline,
                              # untrusted: only plain web links become clickable
                              "url": j.url if (j.url or "").lower().startswith(("https://", "http://")) else ""})
    for d in session.scalars(select(GeneratedDocumentRow).where(GeneratedDocumentRow.created_at >= since)
                             .order_by(GeneratedDocumentRow.id)):
        j = session.get(JobRow, d.job_id)
        rep.documents.append({"id": d.id, "job": d.job_id, "employer": j.employer if j else "",
                              "type": d.doc_type.replace("_", " "), "language": d.language,
                              "status": d.approval_status.replace("_", " "), "file": d.file_path})
    rep.due = [{"when": i.when, "kind": i.kind.replace("_", " "), "job": i.job_id, "title": i.title,
                "employer": i.employer, "overdue": i.when < now.date()} for i in due_items(session, now.date(), 7)]
    src = sources_frame(session, now)
    rep.source_problems = src[src["state"].isin(["failing", "stale", "never collected"])][
        ["source", "state", "error"]].to_dict("records")
    for r in session.scalars(select(RunRow).where(RunRow.started_at >= since)):
        for e in r.errors or []:
            rep.run_errors.append(e.get("error") if not e.get("source") else f"{e['source']}: {e['error']}")
    rep.usage = dict(session.execute(select(ApiUsageRow.purpose, func.count())
                                     .where(ApiUsageRow.called_at >= since).group_by(ApiUsageRow.purpose)).all())
    return rep


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{{ r.subject }}</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;color:#1d2733;max-width:880px;margin:24px auto;padding:0 16px}
 h1{color:#1f3a5f;font-size:22px} h2{color:#1f3a5f;font-size:17px;border-bottom:2px solid #1f3a5f;padding-bottom:3px;margin-top:26px}
 table{border-collapse:collapse;width:100%;font-size:13px} th,td{text-align:left;padding:5px 7px;border-bottom:1px solid #dde3ea;vertical-align:top}
 th{background:#eef2f6} .score{font-weight:600} .warn{color:#a15c00} .bad{color:#b3261e} .ok{color:#1e7b34} .muted{color:#66727f;font-size:12px}
</style></head><body>
<h1>{{ r.subject }}</h1>
<p class="muted">Jobs found since {{ r.since.strftime('%d.%m.%Y') }} · created {{ created }} · nothing has been sent to any employer.</p>

{% if r.steps %}<h2>Today's run</h2><table><tr><th>Step</th><th>Result</th></tr>
{% for s in r.steps %}<tr><td>{{ s.name }}</td><td class="{{ 'ok' if s.ok else 'bad' }}">{{ 'OK' if s.ok else 'FAILED' }} {{ s.detail }}</td></tr>{% endfor %}</table>{% endif %}

<h2>Good matches ({{ r.top_matches|length }})</h2>
{% if r.top_matches %}<table><tr><th>#</th><th>Score</th><th>Job</th><th>Location</th><th>Salary</th><th>Deadline</th><th>AI says</th></tr>
{% for e in r.top_matches %}<tr><td>{{ e.id }}</td><td class="score">{{ '%.0f'|format(e.score) }}</td>
<td>{% if e.url %}<a href="{{ e.url }}">{{ e.title }}</a>{% else %}{{ e.title }}{% endif %}<br><span class="muted">{{ e.employer }}</span>
{% if e.ineligible %}<br><span class="warn">⚠ a mandatory requirement is not met</span>{% endif %}</td>
<td>{{ e.location }}</td><td>{{ e.salary or '-' }}</td><td>{{ e.deadline or '-' }}</td><td>{{ e.recommendation }}</td></tr>{% endfor %}</table>
{% else %}<p>No new jobs above the recommendation threshold.</p>{% endif %}
{% set others = r.evaluated|length - r.top_matches|length %}
{% if others > 0 %}<p class="muted">{{ others }} other evaluated job(s) scored lower - see the dashboard.</p>{% endif %}

{% if r.documents %}<h2>Documents created ({{ r.documents|length }})</h2><table><tr><th>Doc</th><th>Job</th><th>Type</th><th>QA</th></tr>
{% for d in r.documents %}<tr><td>#{{ d.id }}</td><td>#{{ d.job }} {{ d.employer }}</td><td>{{ d.type }} ({{ d.language }})</td>
<td class="{{ 'ok' if 'passed' in d.status else 'bad' }}">{{ d.status }}</td></tr>{% endfor %}</table>
<p class="muted">Read them and approve in the dashboard (Documents) or with: vjh docs approve &lt;id&gt;</p>{% endif %}

<h2>Due in the next 7 days</h2>
{% if r.due %}<table><tr><th>Date</th><th>What</th><th>Job</th></tr>
{% for d in r.due %}<tr><td class="{{ 'bad' if d.overdue else '' }}">{{ d.when }}{{ ' (overdue)' if d.overdue else '' }}</td><td>{{ d.kind }}</td><td>#{{ d.job }} {{ d.title }} · {{ d.employer }}</td></tr>{% endfor %}</table>
{% else %}<p>Nothing due.</p>{% endif %}

{% if r.source_problems or r.run_errors %}<h2>Needs attention</h2><ul>
{% for s in r.source_problems %}<li class="warn">Source {{ s.source }}: {{ s.state }} {{ s.error }}</li>{% endfor %}
{% for e in r.run_errors %}<li class="bad">{{ e }}</li>{% endfor %}</ul>{% endif %}

<h2>Overview</h2>
<p>New jobs: {{ r.new_total }}{% if r.new_by_source %} ({% for s, n in r.new_by_source|dictsort %}{{ s }} {{ n }}{{ ', ' if not loop.last }}{% endfor %}){% endif %}.
AI calls: {% for p, n in r.usage|dictsort %}{{ p }} {{ n }}{{ ', ' if not loop.last }}{% else %}none{% endfor %}.</p>
<p class="muted">Open the dashboard with: vjh dashboard</p>
</body></html>
"""

_env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))


def render_html(rep: DailyReport) -> str:
    return _env.from_string(TEMPLATE).render(r=rep, created=datetime.now().strftime("%d.%m.%Y %H:%M"))


def save_report(rep: DailyReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"daily-{rep.day.isoformat()}.html"
    path.write_text(render_html(rep), encoding="utf-8")
    return path
