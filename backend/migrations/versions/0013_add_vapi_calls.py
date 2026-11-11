"""Answer calls through Vapi, and keep a call's pending booking in the database

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_sessions",
        sa.Column("provider", sa.String(8), nullable=False, server_default="openai"),
    )
    op.create_check_constraint(
        "voice_provider_known", "voice_sessions", "provider IN ('openai', 'vapi')"
    )
    op.add_column(
        "voice_sessions",
        sa.Column(
            "pending_draft_id",
            UUID(as_uuid=True),
            sa.ForeignKey("booking_drafts.id", ondelete="SET NULL"),
        ),
    )


def downgrade() -> None:
    op.drop_column("voice_sessions", "pending_draft_id")
    op.drop_constraint("voice_provider_known", "voice_sessions", type_="check")
    op.drop_column("voice_sessions", "provider")
