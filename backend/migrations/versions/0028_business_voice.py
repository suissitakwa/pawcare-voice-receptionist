"""Give each business the receptionist voice its owner chose

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty keeps the deployment's voice (VOICE_NAME, PHONE_VOICE, VAPI_VOICE_ID).
    op.add_column("businesses", sa.Column("voice", sa.String(16)))
    op.create_check_constraint(
        "business_voice_known", "businesses", "voice IS NULL OR voice IN ('marin', 'cedar')"
    )


def downgrade() -> None:
    op.drop_constraint("business_voice_known", "businesses", type_="check")
    op.drop_column("businesses", "voice")
