"""Link a subscriber's profile to the business created from it

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set once the business is created; deleting the business returns the profile to waiting.
    op.add_column(
        "business_profiles",
        sa.Column(
            "business_id",
            sa.Uuid,
            sa.ForeignKey("businesses.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_business_profiles_business_id", "business_profiles", ["business_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_business_profiles_business_id", "business_profiles", type_="unique")
    op.drop_column("business_profiles", "business_id")
