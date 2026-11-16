"""Phase 2: source ids on jobs, collector info on sources.

Revision ID: 0002_collection
Revises: 0001_initial
Create Date: 2026-11-15

RULE: upgrade() must never drop tables or columns, or delete rows.
Only plain ADD COLUMN / CREATE INDEX here, so SQLite never rebuilds a table.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_collection"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("external_id", sa.String(300), nullable=True))
    op.add_column("jobs", sa.Column("run_id", sa.Integer(), nullable=True))
    op.create_index("ix_jobs_external_id", "jobs", ["external_id"])
    op.add_column("sources", sa.Column("kind", sa.String(30), nullable=True))
    op.add_column("sources", sa.Column("collector", sa.String(30), nullable=True))
    op.add_column("sources", sa.Column("last_jobs_found", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Downgrade is disabled to protect your data. Restore a .bak file instead.")
