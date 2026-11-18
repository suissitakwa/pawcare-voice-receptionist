"""Email the owner about bookings and callback requests

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Where owner notices go. Empty: the business's contact email.
    op.add_column("businesses", sa.Column("notify_email", sa.String(254), nullable=True))
    # Each event is emailed separately from its webhook delivery, with its own retries.
    op.add_column("webhook_events", sa.Column("email_sent_at", sa.DateTime(timezone=True)))
    op.add_column("webhook_events", sa.Column("email_failed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "webhook_events",
        sa.Column("email_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("webhook_events", sa.Column("email_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("webhook_events", sa.Column("email_error", sa.String(200)))
    # Events from before this change are not emailed.
    op.execute("UPDATE webhook_events SET email_failed_at = created_at")


def downgrade() -> None:
    for column in (
        "email_error",
        "email_next_attempt_at",
        "email_attempts",
        "email_failed_at",
        "email_sent_at",
    ):
        op.drop_column("webhook_events", column)
    op.drop_column("businesses", "notify_email")
