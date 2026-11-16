"""SQLAlchemy table definitions. Schema changes go through Alembic migrations only."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    employer: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(Text, index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    published_at: Mapped[date | None] = mapped_column(Date)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)
    deadline: Mapped[date | None] = mapped_column(Date)
    salary: Mapped[dict | None] = mapped_column(JSON)               # models.Salary as dict
    salary_annual_min: Mapped[float | None] = mapped_column(Float)  # only if computable
    employment_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(300), index=True)   # id at the source
    run_id: Mapped[int | None] = mapped_column(Integer)                      # run that found it
    filter_reason: Mapped[str | None] = mapped_column(Text)                  # Stage 1 decision
    duplicate_of: Mapped[int | None] = mapped_column(Integer)                # id of the kept copy


class JobEvaluationRow(Base):
    __tablename__ = "job_evaluations"
    __table_args__ = (
        UniqueConstraint("job_id", "content_hash", "profile_version", "model",
                         name="uq_evaluation_cache_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    overall_score: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(30))
    potentially_ineligible: Mapped[bool] = mapped_column(Boolean)
    eligibility_result: Mapped[str] = mapped_column(String(30))
    professional_suitability: Mapped[float | None] = mapped_column(Float)
    career_attractiveness: Mapped[float | None] = mapped_column(Float)
    cv_profile: Mapped[str] = mapped_column(String(5))
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    missing_qualifications: Mapped[list] = mapped_column(JSON, default=list)
    mandatory_requirements: Mapped[list] = mapped_column(JSON, default=list)
    potential_obstacles: Mapped[list] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text, default="")
    ai_output: Mapped[dict] = mapped_column(JSON)                   # full validated AIEvaluation
    model: Mapped[str] = mapped_column(String(100))
    profile_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime)


class ApplicationRow(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    date_prepared: Mapped[date | None] = mapped_column(Date)
    date_approved: Mapped[date | None] = mapped_column(Date)
    date_submitted: Mapped[date | None] = mapped_column(Date)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    interview_date: Mapped[datetime | None] = mapped_column(DateTime)
    employer_response: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(100))
    documents_submitted: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class ApplicationEventRow(Base):
    """Append-only. UPDATE and DELETE are blocked by database triggers."""

    __tablename__ = "application_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")


class GeneratedDocumentRow(Base):
    __tablename__ = "generated_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10))
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    approval_status: Mapped[str] = mapped_column(String(30), default="draft")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class SourceRow(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    url: Mapped[str] = mapped_column(Text)
    collection_method: Mapped[str] = mapped_column(String(30))
    access_restrictions: Mapped[str] = mapped_column(Text, default="not yet investigated")
    update_frequency: Mapped[str | None] = mapped_column(String(100))
    automatic_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime)
    last_success: Mapped[datetime | None] = mapped_column(DateTime)
    error_status: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(30))
    collector: Mapped[str | None] = mapped_column(String(30))
    last_jobs_found: Mapped[int | None] = mapped_column(Integer)


class UserPreferenceRow(Base):
    __tablename__ = "user_preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[dict | list | str | float | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class FeedbackRow(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="running")
    jobs_collected: Mapped[int] = mapped_column(Integer, default=0)
    jobs_filtered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    jobs_recommended: Mapped[int] = mapped_column(Integer, default=0)
    documents_generated: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    log_path: Mapped[str | None] = mapped_column(Text)


class ApiUsageRow(Base):
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    called_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    model: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class EvidenceRow(Base):
    """Mirror of the evidence registry in personal/master_profile.yaml."""

    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    claim: Mapped[str] = mapped_column(Text)
    supporting_experience: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(30))
    source_detail: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(10))
    verification_status: Mapped[str] = mapped_column(String(20))
    profile_version: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
