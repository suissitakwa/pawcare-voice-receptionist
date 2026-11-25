"""Emergency callbacks, and the number a business's on-call person is texted at

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

OLD = "('health', 'behavior', 'special_handling', 'large_dog', 'booking_help', 'other')"
NEW = (
    "('health', 'behavior', 'special_handling', 'large_dog', 'booking_help', 'other', 'emergency')"
)


def upgrade() -> None:
    op.drop_constraint("callback_reason_known", "callback_requests", type_="check")
    op.create_check_constraint("callback_reason_known", "callback_requests", f"reason IN {NEW}")
    # Texted at once when a caller has an emergency. Empty: the owner is only emailed.
    op.add_column("businesses", sa.Column("on_call_phone", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "on_call_phone")
    # An emergency is still a request for a call back about something staff must decide.
    op.execute(
        "UPDATE callback_requests SET reason = 'special_handling' WHERE reason = 'emergency'"
    )
    op.drop_constraint("callback_reason_known", "callback_requests", type_="check")
    op.create_check_constraint("callback_reason_known", "callback_requests", f"reason IN {OLD}")
