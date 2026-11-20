"""What differs between the kinds of business the receptionist serves.

Pet groomers book by dog size and note the pet's name. Hair salons and barbershops book one
size of each service and have no pet. Everything else, from availability to staff, is the
same for all of them.
"""

from dataclasses import dataclass

# The one size a business without dog sizes books everything as.
STANDARD_SIZE = "standard"


@dataclass(frozen=True)
class Industry:
    key: str
    # "a dog grooming business", said after the business's name.
    described_as: str
    has_pets: bool
    # Customers are "clients" at a salon and "owners" (of the dog) at a groomer.
    customer: str


INDUSTRIES = {
    "pet_grooming": Industry("pet_grooming", "a dog grooming business", True, "owner"),
    "hair_salon": Industry("hair_salon", "a hair salon", False, "client"),
    "barbershop": Industry("barbershop", "a barbershop", False, "client"),
}


def industry_of(business) -> Industry:
    return INDUSTRIES.get(
        getattr(business, "industry", None) or "pet_grooming", INDUSTRIES["pet_grooming"]
    )
