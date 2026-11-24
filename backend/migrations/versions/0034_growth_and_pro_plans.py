"""Two more paid plans: growth (300 calls) and pro (500 calls)

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("business_plan_known", "businesses", type_="check")
    op.create_check_constraint(
        "business_plan_known",
        "businesses",
        "plan IN ('trial', 'starter', 'growth', 'pro', 'unlimited')",
    )


def downgrade() -> None:
    # Growth and Pro become Starter, the nearest plan the old rule allows.
    op.execute("UPDATE businesses SET plan = 'starter' WHERE plan IN ('growth', 'pro')")
    op.drop_constraint("business_plan_known", "businesses", type_="check")
    op.create_check_constraint(
        "business_plan_known", "businesses", "plan IN ('trial', 'starter', 'unlimited')"
    )
