"""Create staff accounts and sessions, and record who cancels or blocks

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_users",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("business_id", "email", name="uq_staff_users_business_email"),
    )
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "staff_id",
            sa.Uuid(),
            sa.ForeignKey("staff_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("bookings", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "bookings",
        sa.Column(
            "cancelled_by",
            sa.Uuid(),
            sa.ForeignKey("staff_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "blocked_periods",
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("staff_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "blocked_periods",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_column("blocked_periods", "created_at")
    op.drop_column("blocked_periods", "created_by")
    op.drop_column("bookings", "cancelled_by")
    op.drop_column("bookings", "cancelled_at")
    op.drop_table("staff_sessions")
    op.drop_table("staff_users")
