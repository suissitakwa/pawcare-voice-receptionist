"""Create scheduling rules, groomers, blocked periods, and bookings

Bookings hold only what availability needs. Customer details and the overlap
constraint arrive with the booking flow.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _business_fk() -> sa.Column:
    return sa.Column(
        "business_id", sa.Uuid(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )


def _groomer_fk(ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        "groomer_id", sa.Uuid(), sa.ForeignKey("groomers.id", ondelete=ondelete), nullable=False
    )


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("slot_increment_minutes", sa.Integer(), nullable=False, server_default="15"),
    )
    op.add_column(
        "businesses", sa.Column("buffer_minutes", sa.Integer(), nullable=False, server_default="15")
    )
    op.add_column(
        "businesses",
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False, server_default="120"),
    )
    op.add_column(
        "businesses",
        sa.Column("booking_horizon_days", sa.Integer(), nullable=False, server_default="30"),
    )
    op.create_check_constraint(
        "slot_increment_positive", "businesses", "slot_increment_minutes > 0"
    )
    op.create_check_constraint("buffer_not_negative", "businesses", "buffer_minutes >= 0")
    op.create_check_constraint("min_notice_not_negative", "businesses", "min_notice_minutes >= 0")
    op.create_check_constraint("horizon_positive", "businesses", "booking_horizon_days > 0")

    op.create_table(
        "groomers",
        _id(),
        _business_fk(),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("business_id", "slug"),
    )
    op.create_table(
        "groomer_hours",
        _id(),
        _groomer_fk(),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.UniqueConstraint("groomer_id", "weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="groomer_hours_weekday_range"),
        sa.CheckConstraint("ends_at > starts_at", name="groomer_hours_end_after_start"),
    )
    op.create_table(
        "groomer_breaks",
        _id(),
        _groomer_fk(),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="groomer_breaks_weekday_range"),
        sa.CheckConstraint("ends_at > starts_at", name="groomer_breaks_end_after_start"),
    )
    op.create_table(
        "blocked_periods",
        _id(),
        _groomer_fk(),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.CheckConstraint("ends_at > starts_at", name="blocked_end_after_start"),
    )
    op.create_index(
        "ix_blocked_periods_groomer_start", "blocked_periods", ["groomer_id", "starts_at"]
    )
    op.create_table(
        "bookings",
        _id(),
        _business_fk(),
        _groomer_fk("RESTRICT"),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column(
            "size_category_id", sa.Uuid(), sa.ForeignKey("size_categories.id"), nullable=False
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupied_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("ends_at > starts_at", name="booking_end_after_start"),
        sa.CheckConstraint("occupied_until >= ends_at", name="booking_buffer_after_end"),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="booking_status_known"),
    )
    op.create_index("ix_bookings_groomer_start", "bookings", ["groomer_id", "starts_at"])


def downgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("blocked_periods")
    op.drop_table("groomer_breaks")
    op.drop_table("groomer_hours")
    op.drop_table("groomers")
    op.drop_constraint("horizon_positive", "businesses")
    op.drop_constraint("min_notice_not_negative", "businesses")
    op.drop_constraint("buffer_not_negative", "businesses")
    op.drop_constraint("slot_increment_positive", "businesses")
    op.drop_column("businesses", "booking_horizon_days")
    op.drop_column("businesses", "min_notice_minutes")
    op.drop_column("businesses", "buffer_minutes")
    op.drop_column("businesses", "slot_increment_minutes")
