"""Phase 8: indexes for the columns every list and the daily run filter on.

Revision ID: 0004_indexes
Revises: 0003_pipeline
Create Date: 2026-11-16

RULE: upgrade() must never drop tables or columns, or delete rows.
Only CREATE INDEX here: no data is changed.
"""
from alembic import op

revision = "0004_indexes"
down_revision = "0003_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_url", "jobs", ["url"])
    op.create_index("ix_jobs_discovered_at", "jobs", ["discovered_at"])
    op.create_index("ix_applications_status", "applications", ["status"])
    op.create_index("ix_api_usage_called_at", "api_usage", ["called_at"])


def downgrade() -> None:
    raise RuntimeError("Downgrade is disabled to protect your data. Restore a .bak file instead.")
