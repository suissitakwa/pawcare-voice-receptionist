"""Record whether a voice session came from the website or a phone call

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_sessions",
        sa.Column("channel", sa.String(8), nullable=False, server_default="web"),
    )
    op.create_check_constraint(
        "voice_channel_known", "voice_sessions", "channel IN ('web', 'phone')"
    )


def downgrade() -> None:
    op.drop_constraint("voice_channel_known", "voice_sessions", type_="check")
    op.drop_column("voice_sessions", "channel")
