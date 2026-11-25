"""What the customer needs done, on bookings, and how each service's price is meant

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("bookings", "booking_drafts"):
        op.add_column(table, sa.Column("job_details", sa.Text(), nullable=True))
        op.create_check_constraint(
            f"{table}_job_details_length", table, "char_length(job_details) <= 500"
        )
    # fixed: the price is the price. from: the lowest it costs. visit_fee: what it costs for
    # someone to come out; any repair is quoted on site.
    op.add_column(
        "services",
        sa.Column("price_kind", sa.String(16), nullable=False, server_default="fixed"),
    )
    op.create_check_constraint(
        "service_price_kind_known", "services", "price_kind IN ('fixed', 'from', 'visit_fee')"
    )


def downgrade() -> None:
    op.drop_constraint("service_price_kind_known", "services", type_="check")
    op.drop_column("services", "price_kind")
    for table in ("bookings", "booking_drafts"):
        op.drop_constraint(f"{table}_job_details_length", table, type_="check")
        op.drop_column(table, "job_details")
