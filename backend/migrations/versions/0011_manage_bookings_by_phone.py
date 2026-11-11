"""Let callers change or cancel a booking during a phone call

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("cancelled_via", sa.String(8)))
    op.create_check_constraint(
        "booking_cancelled_via_known",
        "bookings",
        "cancelled_via IS NULL OR cancelled_via IN ('staff', 'caller')",
    )
    op.add_column("bookings", sa.Column("changed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE bookings SET cancelled_via = 'staff' WHERE status = 'cancelled'")
    op.add_column(
        "voice_sessions",
        sa.Column(
            "managed_booking_id",
            UUID(as_uuid=True),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("voice_sessions", sa.Column("pending_change", JSONB))
    op.add_column(
        "voice_sessions",
        sa.Column("lookup_failures", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("voice_sessions", "lookup_failures")
    op.drop_column("voice_sessions", "pending_change")
    op.drop_column("voice_sessions", "managed_booking_id")
    op.drop_column("bookings", "changed_at")
    op.drop_constraint("booking_cancelled_via_known", "bookings", type_="check")
    op.drop_column("bookings", "cancelled_via")
