"""Text customers their booking confirmations, changes, cancellations and reminders

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

EVENT_COLUMNS = (
    "text_error",
    "text_next_attempt_at",
    "text_attempts",
    "text_failed_at",
    "text_sent_at",
)
BOOKING_COLUMNS = (
    "reminder_error",
    "reminder_next_attempt_at",
    "reminder_attempts",
    "reminder_for",
)


def upgrade() -> None:
    # Off until the owner asks for it: texts cost money and need a registered sender.
    op.add_column(
        "businesses",
        sa.Column("texts_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    # The customer's text about each booking event, with its own retries.
    op.add_column("webhook_events", sa.Column("text_sent_at", sa.DateTime(timezone=True)))
    op.add_column("webhook_events", sa.Column("text_failed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "webhook_events",
        sa.Column("text_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("webhook_events", sa.Column("text_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("webhook_events", sa.Column("text_error", sa.String(200)))
    # Events from before this change are never texted.
    op.execute("UPDATE webhook_events SET text_failed_at = created_at")

    # The start time the reminder was sent (or given up) for. A moved booking has a new
    # start, so it gets a new reminder; the same start never gets two.
    op.add_column("bookings", sa.Column("reminder_for", sa.DateTime(timezone=True)))
    op.add_column(
        "bookings",
        sa.Column("reminder_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("bookings", sa.Column("reminder_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("bookings", sa.Column("reminder_error", sa.String(200)))
    # Bookings made before this change get no reminder.
    op.execute("UPDATE bookings SET reminder_for = starts_at")


def downgrade() -> None:
    for column in BOOKING_COLUMNS:
        op.drop_column("bookings", column)
    for column in EVENT_COLUMNS:
        op.drop_column("webhook_events", column)
    op.drop_column("businesses", "texts_enabled")
