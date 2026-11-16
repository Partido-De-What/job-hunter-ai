"""Phase 3: Stage 1 filter reason and duplicate link on jobs.

Revision ID: 0003_pipeline
Revises: 0002_collection
Create Date: 2026-11-15

RULE: upgrade() must never drop tables or columns, or delete rows.
Only plain ADD COLUMN, so SQLite never rebuilds a table.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_pipeline"
down_revision = "0002_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("filter_reason", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("duplicate_of", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Downgrade is disabled to protect your data. Restore a .bak file instead.")
