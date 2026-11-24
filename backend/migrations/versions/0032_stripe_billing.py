"""Card payment through Stripe: each business's customer, subscription and its status

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("stripe_customer_id", sa.String(64)))
    op.add_column("businesses", sa.Column("stripe_subscription_id", sa.String(64)))
    op.add_column("businesses", sa.Column("billing_status", sa.String(32)))
    op.create_unique_constraint(
        "business_stripe_customer_unique", "businesses", ["stripe_customer_id"]
    )
    op.create_unique_constraint(
        "business_stripe_subscription_unique", "businesses", ["stripe_subscription_id"]
    )


def downgrade() -> None:
    op.drop_constraint("business_stripe_subscription_unique", "businesses", type_="unique")
    op.drop_constraint("business_stripe_customer_unique", "businesses", type_="unique")
    op.drop_column("businesses", "billing_status")
    op.drop_column("businesses", "stripe_subscription_id")
    op.drop_column("businesses", "stripe_customer_id")
