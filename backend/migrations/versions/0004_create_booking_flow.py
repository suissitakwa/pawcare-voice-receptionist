"""Create booking drafts, customer details, idempotency, and the overlap constraint

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("groomer_id", sa.Uuid(), sa.ForeignKey("groomers.id"), nullable=False),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column(
            "size_category_id", sa.Uuid(), sa.ForeignKey("size_categories.id"), nullable=False
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupied_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("owner_name", sa.String(80), nullable=False),
        sa.Column("owner_email", sa.String(254), nullable=True),
        sa.Column("owner_phone", sa.String(20), nullable=True),
        sa.Column("pet_name", sa.String(40), nullable=False),
        sa.Column("summary", JSONB(), nullable=False),
        sa.Column("summary_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "owner_email IS NOT NULL OR owner_phone IS NOT NULL", name="draft_has_contact"
        ),
    )

    # The bookings table holds no rows before this revision, so new required columns are safe.
    op.add_column("bookings", sa.Column("reference", sa.String(16), nullable=False))
    op.add_column("bookings", sa.Column("price_cents", sa.Integer(), nullable=False))
    op.add_column("bookings", sa.Column("owner_name", sa.String(80), nullable=False))
    op.add_column("bookings", sa.Column("owner_email", sa.String(254), nullable=True))
    op.add_column("bookings", sa.Column("owner_phone", sa.String(20), nullable=True))
    op.add_column("bookings", sa.Column("pet_name", sa.String(40), nullable=False))
    op.add_column("bookings", sa.Column("summary", JSONB(), nullable=False))
    op.add_column("bookings", sa.Column("idempotency_key", sa.String(128), nullable=False))
    op.add_column("bookings", sa.Column("request_hash", sa.String(64), nullable=False))
    op.add_column(
        "bookings",
        sa.Column(
            "draft_id",
            sa.Uuid(),
            sa.ForeignKey("booking_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_bookings_reference", "bookings", ["reference"])
    op.create_unique_constraint(
        "uq_bookings_idempotency_key", "bookings", ["business_id", "idempotency_key"]
    )
    op.create_unique_constraint("uq_bookings_draft_id", "bookings", ["draft_id"])
    op.create_check_constraint(
        "booking_has_contact", "bookings", "owner_email IS NOT NULL OR owner_phone IS NOT NULL"
    )
    # The guarantee against double booking. Each confirmed booking occupies its appointment
    # plus its trailing buffer, and no two such ranges may overlap for one groomer, however
    # many requests arrive at once.
    op.execute(
        """
        ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
        EXCLUDE USING gist (
            groomer_id WITH =,
            tstzrange(starts_at, occupied_until, '[)') WITH &&
        ) WHERE (status = 'confirmed')
        """
    )


def downgrade() -> None:
    op.drop_constraint("bookings_no_overlap", "bookings")
    op.drop_constraint("booking_has_contact", "bookings")
    op.drop_constraint("uq_bookings_draft_id", "bookings")
    op.drop_constraint("uq_bookings_idempotency_key", "bookings")
    op.drop_constraint("uq_bookings_reference", "bookings")
    for column in (
        "draft_id",
        "request_hash",
        "idempotency_key",
        "summary",
        "pet_name",
        "owner_phone",
        "owner_email",
        "owner_name",
        "price_cents",
        "reference",
    ):
        op.drop_column("bookings", column)
    op.drop_table("booking_drafts")
