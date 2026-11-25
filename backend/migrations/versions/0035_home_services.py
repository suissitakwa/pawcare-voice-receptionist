"""Home services as a fourth kind of business, for sign-up profiles and live businesses

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

OLD = "('pet_grooming', 'hair_salon', 'barbershop')"
NEW = "('pet_grooming', 'hair_salon', 'barbershop', 'home_services')"


def replace_checks(types: str) -> None:
    op.drop_constraint("business_industry_known", "businesses", type_="check")
    op.create_check_constraint("business_industry_known", "businesses", f"industry IN {types}")
    op.drop_constraint("profile_business_type_known", "business_profiles", type_="check")
    op.create_check_constraint(
        "profile_business_type_known",
        "business_profiles",
        f"business_type IS NULL OR business_type IN {types}",
    )


def upgrade() -> None:
    replace_checks(NEW)


def downgrade() -> None:
    # The old rules have no home services. A profile goes back to a draft with no type, for
    # the owner to choose again; a business becomes the nearest kind that can still be mobile.
    op.execute(
        "UPDATE business_profiles SET status = 'draft', business_type = NULL "
        "WHERE business_type = 'home_services'"
    )
    op.execute("UPDATE businesses SET industry = 'pet_grooming' WHERE industry = 'home_services'")
    replace_checks(OLD)
