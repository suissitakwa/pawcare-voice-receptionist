"""Mark phone calls answered only to take a message

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A call answered after the daily limit: it may only take a callback request.
    op.add_column(
        "voice_sessions",
        sa.Column("message_only", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("voice_sessions", "message_only")
