"""Let the outbox hold notices that belong to no business yet, such as a new sign-up

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("webhook_events", "business_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM webhook_events WHERE business_id IS NULL")
    op.alter_column("webhook_events", "business_id", nullable=False)
