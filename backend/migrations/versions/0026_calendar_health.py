"""Check each connected calendar regularly and remember whether it works

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

COLUMNS = ("alerted_at", "problem_since", "problem", "problem_code", "checked_at")


def upgrade() -> None:
    # The last regular check of a calendar, the problem it found (if any), since when, and
    # when the owner was told, so one outage sends one email.
    op.add_column("business_integrations", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.add_column("business_integrations", sa.Column("problem_code", sa.String(40)))
    op.add_column("business_integrations", sa.Column("problem", sa.String(300)))
    op.add_column("business_integrations", sa.Column("problem_since", sa.DateTime(timezone=True)))
    op.add_column("business_integrations", sa.Column("alerted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column("business_integrations", column)
