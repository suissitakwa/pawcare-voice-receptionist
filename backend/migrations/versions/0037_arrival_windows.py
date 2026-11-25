"""Arrival windows: a business can offer "arriving between 8 and 11" instead of exact times

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty: exact start times, as before.
    op.add_column("businesses", sa.Column("arrival_window_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "business_arrival_window_range",
        "businesses",
        "arrival_window_minutes IS NULL OR arrival_window_minutes BETWEEN 60 AND 480",
    )


def downgrade() -> None:
    op.drop_constraint("business_arrival_window_range", "businesses", type_="check")
    op.drop_column("businesses", "arrival_window_minutes")
