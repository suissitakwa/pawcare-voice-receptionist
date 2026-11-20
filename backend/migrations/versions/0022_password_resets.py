"""One-time links to choose a new password, for sign-up and staff accounts

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "subscriber_account_id",
            sa.Uuid(),
            sa.ForeignKey("subscriber_accounts.id", ondelete="CASCADE"),
        ),
        sa.Column("staff_user_id", sa.Uuid(), sa.ForeignKey("staff_users.id", ondelete="CASCADE")),
        # Only a hash: the link itself is emailed and never stored here.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(subscriber_account_id IS NULL) <> (staff_user_id IS NULL)",
            name="password_reset_one_account",
        ),
    )
    op.create_index("ix_password_resets_created_at", "password_resets", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_password_resets_created_at", "password_resets")
    op.drop_table("password_resets")
