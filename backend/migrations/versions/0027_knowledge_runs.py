"""Remember when owners ask for answers drafted from their pages and documents

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per source read, so each owner can ask only so often.
    op.create_table(
        "knowledge_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["subscriber_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_runs_account_id", "knowledge_runs", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_runs_account_id", table_name="knowledge_runs")
    op.drop_table("knowledge_runs")
