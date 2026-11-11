"""Track Google Calendar sync for each booking

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("calendar_dirty", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "bookings",
        sa.Column("calendar_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("bookings", sa.Column("calendar_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("bookings", sa.Column("calendar_error", sa.String(200)))
    op.add_column("bookings", sa.Column("calendar_synced_at", sa.DateTime(timezone=True)))
    # Past and cancelled bookings never need an event; upcoming ones are added on connect.
    op.execute(
        "UPDATE bookings SET calendar_dirty = false "
        "WHERE status <> 'confirmed' OR starts_at < now()"
    )
    op.create_index(
        "ix_bookings_calendar_dirty",
        "bookings",
        ["calendar_next_attempt_at"],
        postgresql_where=sa.text("calendar_dirty"),
    )


def downgrade() -> None:
    op.drop_index("ix_bookings_calendar_dirty", table_name="bookings")
    for column in (
        "calendar_synced_at",
        "calendar_error",
        "calendar_next_attempt_at",
        "calendar_attempts",
        "calendar_dirty",
    ):
        op.drop_column("bookings", column)
