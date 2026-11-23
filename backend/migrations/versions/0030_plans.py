"""Give each business a plan: a free trial, the paid plan, or unlimited

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every business so far (the demo and pilots) keeps working as before: unlimited.
    op.add_column(
        "businesses",
        sa.Column("plan", sa.String(16), server_default="unlimited", nullable=False),
    )
    op.add_column("businesses", sa.Column("trial_ends_at", sa.DateTime(timezone=True)))
    op.add_column("businesses", sa.Column("upgrade_requested_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "business_plan_known", "businesses", "plan IN ('trial', 'starter', 'unlimited')"
    )


def downgrade() -> None:
    op.drop_constraint("business_plan_known", "businesses", type_="check")
    op.drop_column("businesses", "upgrade_requested_at")
    op.drop_column("businesses", "trial_ends_at")
    op.drop_column("businesses", "plan")
