"""When a cancelled card subscription stops: the end of the period already paid for

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("billing_ends_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("businesses", "billing_ends_at")
