"""Create the outbox of events sent to the business's automation webhook

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="webhook_attempts_positive"),
        sa.CheckConstraint(
            "delivered_at IS NULL OR failed_at IS NULL", name="webhook_one_final_state"
        ),
    )
    op.create_index(
        "ix_webhook_events_due",
        "webhook_events",
        ["next_attempt_at"],
        postgresql_where=sa.text("delivered_at IS NULL AND failed_at IS NULL"),
    )
    op.create_index("ix_webhook_events_created", "webhook_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_created", table_name="webhook_events")
    op.drop_index("ix_webhook_events_due", table_name="webhook_events")
    op.drop_table("webhook_events")
