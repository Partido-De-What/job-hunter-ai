"""Application tracking. History is append-only: each status change is a new event."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from .common import ApplicationStatus


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    job_id: int
    status: ApplicationStatus = ApplicationStatus.NEW
    date_prepared: date | None = None
    date_approved: date | None = None
    date_submitted: date | None = None
    follow_up_date: date | None = None
    interview_date: datetime | None = None
    employer_response: str | None = None
    result: str | None = None
    documents_submitted: list[int] = []   # generated_documents ids
    notes: str = ""


class ApplicationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: int
    from_status: ApplicationStatus | None = None
    to_status: ApplicationStatus
    occurred_at: datetime
    note: str = ""


class Feedback(BaseModel):
    """Why the user accepted or rejected a recommendation. Used to adjust search
    priorities later, never to silently change the scoring model."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    accepted: bool
    reason: str = Field(min_length=1)
    created_at: datetime
