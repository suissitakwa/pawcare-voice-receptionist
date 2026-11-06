"""Create callback requests, and link bookings and tool counts to voice sessions

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "callback_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "voice_session_id",
            sa.Uuid(),
            sa.ForeignKey("voice_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by",
            sa.Uuid(),
            sa.ForeignKey("staff_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("phone IS NOT NULL OR email IS NOT NULL", name="callback_has_contact"),
        sa.CheckConstraint("status IN ('open', 'done')", name="callback_status_known"),
        sa.CheckConstraint(
            "reason IN ('health', 'behavior', 'special_handling', 'large_dog', "
            "'booking_help', 'other')",
            name="callback_reason_known",
        ),
    )
    op.add_column(
        "voice_sessions",
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "voice_sessions",
        sa.Column("tool_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("voice_sessions", sa.Column("booking_reference", sa.String(16), nullable=True))
    for table in ("booking_drafts", "bookings"):
        op.add_column(
            table,
            sa.Column(
                "voice_session_id",
                sa.Uuid(),
                sa.ForeignKey("voice_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("bookings", "voice_session_id")
    op.drop_column("booking_drafts", "voice_session_id")
    op.drop_column("voice_sessions", "booking_reference")
    op.drop_column("voice_sessions", "tool_failures")
    op.drop_column("voice_sessions", "tool_calls")
    op.drop_table("callback_requests")
