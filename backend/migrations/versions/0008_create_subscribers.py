"""Create subscriber accounts, sessions, business profiles, and profile documents

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

BUSINESS_TYPES = "('pet_grooming', 'hair_salon', 'barbershop')"


def created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "subscriber_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("full_name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        # A hash of the sign-up address, used only to rate-limit sign-ups.
        sa.Column("client_hash", sa.String(64), nullable=False),
        created_at(),
        sa.CheckConstraint("email = lower(email)", name="email_lowercase"),
    )
    op.create_index(
        "ix_subscriber_accounts_client_created",
        "subscriber_accounts",
        ["client_hash", "created_at"],
    )
    op.create_table(
        "subscriber_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("subscriber_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
    )
    op.create_table(
        "business_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("subscriber_accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("business_name", sa.String(120), nullable=True),
        sa.Column("business_type", sa.String(32), nullable=True),
        # Everything else, validated by the API. Promoted to columns once it drives calls.
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('draft', 'submitted')", name="profile_status_known"),
        sa.CheckConstraint(
            f"business_type IS NULL OR business_type IN {BUSINESS_TYPES}",
            name="profile_business_type_known",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR (submitted_at IS NOT NULL AND business_name IS NOT NULL "
            "AND business_type IS NOT NULL)",
            name="profile_submitted_complete",
        ),
    )
    op.create_table(
        "profile_documents",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("business_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(120), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        created_at(),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 5242880", name="document_size"),
        sa.UniqueConstraint("profile_id", "sha256", name="uq_profile_documents_content"),
    )


def downgrade() -> None:
    op.drop_table("profile_documents")
    op.drop_table("business_profiles")
    op.drop_table("subscriber_sessions")
    op.drop_index("ix_subscriber_accounts_client_created", table_name="subscriber_accounts")
    op.drop_table("subscriber_accounts")
