"""Give each business an industry, so salons and barbershops get their own receptionist

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("industry", sa.String(20), nullable=False, server_default="pet_grooming"),
    )
    op.create_check_constraint(
        "business_industry_known",
        "businesses",
        "industry IN ('pet_grooming', 'hair_salon', 'barbershop')",
    )


def downgrade() -> None:
    op.drop_constraint("business_industry_known", "businesses", type_="check")
    op.drop_column("businesses", "industry")
