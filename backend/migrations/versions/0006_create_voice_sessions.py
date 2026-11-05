"""Create voice sessions for quotas, rate limits, and outcomes

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="started"),
        sa.Column("end_reason", sa.String(32), nullable=True),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('test', 'live')", name="voice_mode_known"),
        sa.CheckConstraint("status IN ('started', 'ended')", name="voice_status_known"),
    )
    op.create_index(
        "ix_voice_sessions_business_started", "voice_sessions", ["business_id", "started_at"]
    )
    op.create_index(
        "ix_voice_sessions_client_started", "voice_sessions", ["client_hash", "started_at"]
    )


def downgrade() -> None:
    op.drop_table("voice_sessions")
