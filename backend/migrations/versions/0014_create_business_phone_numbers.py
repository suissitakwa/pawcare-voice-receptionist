"""Route each phone call to the business whose number was dialed

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_phone_numbers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "business_id",
            UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("number", name="uq_business_phone_numbers_number"),
        sa.CheckConstraint("number ~ '^\\+[1-9][0-9]{6,14}$'", name="phone_number_e164"),
    )
    op.create_index(
        "ix_business_phone_numbers_business_id", "business_phone_numbers", ["business_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_business_phone_numbers_business_id", "business_phone_numbers")
    op.drop_table("business_phone_numbers")
