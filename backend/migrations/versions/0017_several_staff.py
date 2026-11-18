"""Let a business have several staff: who does which service, and blocks for everyone

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A staff member with no rows here does every service.
    op.create_table(
        "groomer_services",
        sa.Column(
            "groomer_id",
            sa.Uuid,
            sa.ForeignKey("groomers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "service_id",
            sa.Uuid,
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # A block belongs to a business; without a staff member it closes the whole business.
    op.add_column(
        "blocked_periods",
        sa.Column(
            "business_id",
            sa.Uuid,
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE blocked_periods SET business_id = groomers.business_id
        FROM groomers WHERE groomers.id = blocked_periods.groomer_id
        """
    )
    op.alter_column("blocked_periods", "business_id", nullable=False)
    op.alter_column("blocked_periods", "groomer_id", nullable=True)
    op.create_index("ix_blocked_periods_business_id", "blocked_periods", ["business_id"])


def downgrade() -> None:
    # Business-wide blocks have no staff member to fall back to.
    op.execute("DELETE FROM blocked_periods WHERE groomer_id IS NULL")
    op.drop_index("ix_blocked_periods_business_id", "blocked_periods")
    op.alter_column("blocked_periods", "groomer_id", nullable=False)
    op.drop_column("blocked_periods", "business_id")
    op.drop_table("groomer_services")
