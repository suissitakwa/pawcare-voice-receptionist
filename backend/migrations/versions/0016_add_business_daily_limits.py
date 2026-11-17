"""Let each business have its own daily voice and phone limits

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("voice_daily_limit", sa.Integer, nullable=True))
    op.add_column("businesses", sa.Column("phone_daily_limit", sa.Integer, nullable=True))
    op.create_check_constraint(
        "business_limits_not_negative",
        "businesses",
        "(voice_daily_limit IS NULL OR voice_daily_limit >= 0) "
        "AND (phone_daily_limit IS NULL OR phone_daily_limit >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("business_limits_not_negative", "businesses", type_="check")
    op.drop_column("businesses", "phone_daily_limit")
    op.drop_column("businesses", "voice_daily_limit")
