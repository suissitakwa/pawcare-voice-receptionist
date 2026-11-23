"""Remember which phone numbers came from a sign-up

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A number registered from an owner's sign-up may be taken by a newer sign-up with the
    # same number; one registered by hand never is.
    op.add_column(
        "business_phone_numbers",
        sa.Column("from_signup", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("business_phone_numbers", "from_signup")
