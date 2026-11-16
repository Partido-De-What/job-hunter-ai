"""Feedback on recommendations and a report that SUGGESTS search-priority changes.
Spec Part 11: improve future priorities without silently changing the scoring model -
so nothing here edits configuration; the user decides."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.tables import FeedbackRow, JobEvaluationRow, JobRow
from ..models.common import ApplicationStatus as S
from .service import TrackerError, set_status

STOP = set(["a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "at", "by", "from", "is", "are", "was", "be", "this", "that", "it", "its", "not", "no", "too", "very", "job", "role", "position", "ad", "work", "der", "die", "das", "und", "oder", "ein", "eine", "nicht", "zu", "mit", "für", "im", "in", "am", "von", "ist", "sind", "kein", "keine", "m", "w", "d", "f", "x", "mwd"])
MIN_PATTERN_COUNT = 3


def add_feedback(session: Session, job_id: int, accepted: bool, reason: str,
                 now: datetime | None = None) -> FeedbackRow:
    reason = (reason or "").strip()
    if not reason:
        raise TrackerError("please give a short reason - it is what makes the feedback useful")
    if session.get(JobRow, job_id) is None:
        raise TrackerError(f"no job #{job_id}")
    row = FeedbackRow(job_id=job_id, accepted=accepted, reason=reason, created_at=now or datetime.now())
    session.add(row)
    return row


def dismiss(session: Session, job_id: int, reason: str, now: datetime | None = None):
    """'Not for me': archive the application and record why."""
    add_feedback(session, job_id, False, reason, now)
    return set_status(session, job_id, S.ARCHIVED.value, note=f"dismissed: {reason}", now=now)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zäöüß]{3,}", (text or "").lower()) if w not in STOP]


@dataclass
class FeedbackReport:
    accepted: int = 0
    rejected: int = 0
    avg_score_accepted: float | None = None
    avg_score_rejected: float | None = None
    reason_words: list[tuple[str, int]] = field(default_factory=list)
    rejected_title_words: list[tuple[str, int]] = field(default_factory=list)
    rejected_employers: list[tuple[str, int]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        fmt = lambda pairs: ", ".join(f"{w} ({n})" for w, n in pairs) or "-"   # noqa: E731
        avg = lambda v: f"{v:.0f}" if v is not None else "-"                   # noqa: E731
        lines = ["# Feedback report", "", f"Created: {datetime.now():%Y-%m-%d %H:%M}", "",
                 f"- Accepted recommendations: {self.accepted} (average AI score {avg(self.avg_score_accepted)})",
                 f"- Dismissed recommendations: {self.rejected} (average AI score {avg(self.avg_score_rejected)})",
                 f"- Words in your reasons: {fmt(self.reason_words)}",
                 f"- Frequent title words in dismissed jobs: {fmt(self.rejected_title_words)}",
                 f"- Employers you dismissed more than once: {fmt(self.rejected_employers)}", "",
                 "## Suggestions (nothing has been changed - you decide)", ""]
        lines += [f"- {s}" for s in self.suggestions] or ["- No clear pattern yet. Keep giving feedback."]
        return "\n".join(lines) + "\n"


def build_report(session: Session, recommend_min_score: float, title_patterns_excluded: list[str]) -> FeedbackReport:
    rep = FeedbackReport()
    scores = {True: [], False: []}
    reason_words: Counter = Counter()
    title_words: Counter = Counter()
    employers: Counter = Counter()
    accepted_titles: Counter = Counter()
    for fb in session.scalars(select(FeedbackRow)):
        job = session.get(JobRow, fb.job_id)
        ev = session.scalar(select(JobEvaluationRow).where(JobEvaluationRow.job_id == fb.job_id)
                            .order_by(JobEvaluationRow.evaluated_at.desc(), JobEvaluationRow.id.desc()).limit(1))
        if ev:
            scores[fb.accepted].append(ev.overall_score)
        reason_words.update(set(_words(fb.reason)))
        if fb.accepted:
            rep.accepted += 1
            accepted_titles.update(set(_words(job.title)))
        else:
            rep.rejected += 1
            title_words.update(set(_words(job.title)))
            employers[job.employer] += 1
    rep.avg_score_accepted = sum(scores[True]) / len(scores[True]) if scores[True] else None
    rep.avg_score_rejected = sum(scores[False]) / len(scores[False]) if scores[False] else None
    rep.reason_words = reason_words.most_common(8)
    rep.rejected_title_words = [(w, n) for w, n in title_words.most_common(8) if n >= 2]
    rep.rejected_employers = [(e, n) for e, n in employers.most_common(5) if n >= 2]

    excluded = " ".join(title_patterns_excluded).lower()
    for word, n in title_words.most_common():
        if n >= MIN_PATTERN_COUNT and accepted_titles[word] == 0 and word not in excluded:
            rep.suggestions.append(
                f"You dismissed {n} jobs with '{word}' in the title and accepted none. Consider adding "
                f"\"{word}\" to relevance.exclude_title_patterns in config/search_terms.yaml.")
    for emp, n in rep.rejected_employers:
        if n >= MIN_PATTERN_COUNT:
            rep.suggestions.append(f"You dismissed {n} jobs from {emp}. If this employer is not for you, "
                                   "say so and it can be excluded at Stage 1.")
    if rep.avg_score_rejected is not None and rep.rejected >= MIN_PATTERN_COUNT \
            and rep.avg_score_rejected >= recommend_min_score:
        rep.suggestions.append(
            f"Jobs you dismissed scored {rep.avg_score_rejected:.0f} on average - above your recommendation "
            f"threshold ({recommend_min_score:.0f}). The scores may not reflect what matters to you: read the "
            "reasons above, and consider adding these preferences to your profile or raising "
            "thresholds.recommend_min_score in config/settings.yaml.")
    if rep.avg_score_accepted is not None and rep.accepted >= MIN_PATTERN_COUNT \
            and rep.avg_score_accepted < recommend_min_score:
        rep.suggestions.append(
            f"Jobs you accepted scored {rep.avg_score_accepted:.0f} on average - below the recommendation "
            f"threshold ({recommend_min_score:.0f}). Consider lowering thresholds.recommend_min_score so "
            "similar jobs are not missed.")
    return rep


def save_report(rep: FeedbackReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"feedback-{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(rep.to_markdown(), encoding="utf-8")
    return path
