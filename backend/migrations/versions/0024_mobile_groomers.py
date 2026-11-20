"""Mobile groomers: they come to the customer, inside a service area

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses", sa.Column("is_mobile", sa.Boolean, nullable=False, server_default=sa.false())
    )
    # Five-digit ZIP codes they travel to. Empty: anywhere, as long as an address is given.
    op.add_column(
        "businesses",
        sa.Column(
            "service_zip_codes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # How the area is said to customers, for example "San Jose, Campbell and Santa Clara".
    op.add_column(
        "businesses", sa.Column("service_area", sa.String(200), nullable=False, server_default="")
    )
    # Where the appointment happens, for a mobile business.
    for table in ("bookings", "booking_drafts"):
        op.add_column(table, sa.Column("service_address", sa.String(200)))


def downgrade() -> None:
    for table in ("bookings", "booking_drafts"):
        op.drop_column(table, "service_address")
    op.drop_column("businesses", "service_area")
    op.drop_column("businesses", "service_zip_codes")
    op.drop_column("businesses", "is_mobile")
