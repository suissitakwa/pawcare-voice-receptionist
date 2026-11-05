"""Create service catalog, business hours, and FAQs

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _business_fk() -> sa.Column:
    return sa.Column(
        "business_id", sa.Uuid(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.create_table(
        "size_categories",
        _id(),
        _business_fk(),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column(
            "requires_staff_assessment", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("business_id", "slug"),
    )
    op.create_table(
        "services",
        _id(),
        _business_fk(),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("business_id", "slug"),
    )
    op.create_table(
        "service_size_rules",
        _id(),
        sa.Column(
            "service_id",
            sa.Uuid(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "size_category_id",
            sa.Uuid(),
            sa.ForeignKey("size_categories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.UniqueConstraint("service_id", "size_category_id"),
        sa.CheckConstraint("duration_minutes > 0", name="duration_positive"),
        sa.CheckConstraint("price_cents >= 0", name="price_not_negative"),
    )
    op.create_table(
        "business_hours",
        _id(),
        _business_fk(),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        sa.UniqueConstraint("business_id", "weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        sa.CheckConstraint("closes_at > opens_at", name="closes_after_opens"),
    )
    op.create_table(
        "faqs",
        _id(),
        _business_fk(),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("business_id", "slug"),
    )


def downgrade() -> None:
    op.drop_table("faqs")
    op.drop_table("business_hours")
    op.drop_table("service_size_rules")
    op.drop_table("services")
    op.drop_table("size_categories")
