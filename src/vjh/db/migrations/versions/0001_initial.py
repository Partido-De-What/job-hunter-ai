"""Initial schema: spec Part 6 tables + runs, api_usage, application_events, feedback, evidence.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15

RULE: upgrade() must never drop tables or columns, or delete rows.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("employer", sa.String(300), nullable=False),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("salary", sa.JSON(), nullable=True),
        sa.Column("salary_annual_min", sa.Float(), nullable=True),
        sa.Column("employment_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])

    op.create_table(
        "job_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("potentially_ineligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_result", sa.String(30), nullable=False),
        sa.Column("professional_suitability", sa.Float(), nullable=True),
        sa.Column("career_attractiveness", sa.Float(), nullable=True),
        sa.Column("cv_profile", sa.String(5), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("missing_qualifications", sa.JSON(), nullable=False),
        sa.Column("mandatory_requirements", sa.JSON(), nullable=False),
        sa.Column("potential_obstacles", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("ai_output", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "content_hash", "profile_version", "model",
                            name="uq_evaluation_cache_key"),
    )
    op.create_index("ix_job_evaluations_job_id", "job_evaluations", ["job_id"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("date_prepared", sa.Date(), nullable=True),
        sa.Column("date_approved", sa.Date(), nullable=True),
        sa.Column("date_submitted", sa.Date(), nullable=True),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("interview_date", sa.DateTime(), nullable=True),
        sa.Column("employer_response", sa.Text(), nullable=True),
        sa.Column("result", sa.String(100), nullable=True),
        sa.Column("documents_submitted", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
    )
    op.create_index("ix_application_events_application_id", "application_events", ["application_id"])
    # Application history is append-only.
    op.execute(
        "CREATE TRIGGER application_events_no_update BEFORE UPDATE ON application_events "
        "BEGIN SELECT RAISE(ABORT, 'application_events is append-only'); END;"
    )
    op.execute(
        "CREATE TRIGGER application_events_no_delete BEFORE DELETE ON application_events "
        "BEGIN SELECT RAISE(ABORT, 'application_events is append-only'); END;"
    )

    op.create_table(
        "generated_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approval_status", sa.String(30), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
    )
    op.create_index("ix_generated_documents_job_id", "generated_documents", ["job_id"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("collection_method", sa.String(30), nullable=False),
        sa.Column("access_restrictions", sa.Text(), nullable=False),
        sa.Column("update_frequency", sa.String(100), nullable=True),
        sa.Column("automatic_supported", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_checked", sa.DateTime(), nullable=True),
        sa.Column("last_success", sa.DateTime(), nullable=True),
        sa.Column("error_status", sa.Text(), nullable=True),
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(100), nullable=False, unique=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_feedback_job_id", "feedback", ["job_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("jobs_collected", sa.Integer(), nullable=False),
        sa.Column("jobs_filtered", sa.Integer(), nullable=False),
        sa.Column("jobs_evaluated", sa.Integer(), nullable=False),
        sa.Column("jobs_recommended", sa.Integer(), nullable=False),
        sa.Column("documents_generated", sa.Integer(), nullable=False),
        sa.Column("api_calls", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("log_path", sa.Text(), nullable=True),
    )

    op.create_table(
        "api_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("called_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_api_usage_run_id", "api_usage", ["run_id"])

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("supporting_experience", sa.String(100), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_detail", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("verification_status", sa.String(20), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    # Intentionally not implemented: downgrading would delete data.
    raise RuntimeError("Downgrade is disabled to protect your data. Restore a .bak file instead.")
