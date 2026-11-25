"""What differs between the kinds of business the receptionist serves.

Pet groomers book by dog size and note the pet's name. Hair salons and barbershops book one
size of each service and have no pet. Home services (plumbers first) book one size too, and
always go to the customer, so every booking needs an address in their area. Everything else,
from availability to staff, is the same for all of them.
"""

from dataclasses import dataclass

# The one size a business without dog sizes books everything as.
STANDARD_SIZE = "standard"

SALON_ADVICE = (
    "Never give medical advice. For questions about allergies, scalp or skin conditions, or "
    "anything the staff should decide, such as a big color change, say a stylist needs to "
    "review it and offer a callback."
)
SALON_SPECIAL = (
    "Use health or special_handling for allergies, scalp or skin conditions, or a service the "
    "staff should decide on"
)


@dataclass(frozen=True)
class Industry:
    key: str
    # "a dog grooming business", said after the business's name.
    described_as: str
    has_pets: bool
    # Customers are "clients" at a salon and "owners" (of the dog) at a groomer.
    customer: str
    # Goes to every customer: always mobile, whatever the profile says.
    always_mobile: bool = False
    # What the receptionist must not advise on, and what it hands to staff (businesses
    # without pets; the groomer's wording is about the dog and lives with the instructions).
    advice: str = SALON_ADVICE
    # Which callback reasons fit a question staff must answer, in a message-only call.
    special_reasons: str = SALON_SPECIAL
    # Said before anything else when the caller may be in danger. Empty: nothing to add.
    safety: str = ""
    # Every booking notes what the customer needs done, in their words.
    asks_job_details: bool = False
    # Callers with an emergency are put through to the on-call person as an urgent callback.
    takes_emergencies: bool = False


EMERGENCY = (
    "An emergency is water leaking or flooding now, no water at all, sewage backing up, no "
    "heat in freezing weather, or a gas smell once the caller is safely outside. For an "
    "emergency, don't look for a booking time: take the caller's name, a phone number, the "
    "address and what is happening, read them back, wait for yes, and call "
    "create_callback_request with reason emergency and that sentence as the note. Then tell "
    "them the team is being alerted right away and will call them back, and say you can't "
    "promise how soon. Anything that can wait is booked as usual."
)

HOME_SAFETY = (
    "Safety comes first. If the caller smells gas, tell them to leave the building now and "
    "call the gas company or 911 from outside, and don't book or take details until they say "
    "they are safe. If water is flooding, tell them to turn off the main water valve if they "
    "can do it safely. Give no other repair instructions."
)

INDUSTRIES = {
    "pet_grooming": Industry("pet_grooming", "a dog grooming business", True, "owner"),
    "hair_salon": Industry("hair_salon", "a hair salon", False, "client"),
    "barbershop": Industry("barbershop", "a barbershop", False, "client"),
    "home_services": Industry(
        "home_services",
        "a home services business that sends a technician to the customer",
        False,
        "customer",
        always_mobile=True,
        advice=(
            "Never diagnose a problem or quote the cost of a repair. For anything a "
            "technician should decide, say a technician needs to look at it and offer a "
            "callback."
        ),
        special_reasons=(
            "Use emergency for water leaking or flooding now, no water, sewage backing up, no "
            "heat in freezing weather, or a gas smell once the caller is safe, and "
            "special_handling for a job the technician should decide on"
        ),
        safety=HOME_SAFETY,
        asks_job_details=True,
        takes_emergencies=True,
    ),
}

# The kinds of business that can go to their customers.
MOBILE_INDUSTRIES = {"pet_grooming", "home_services"}


def industry_of(business) -> Industry:
    return INDUSTRIES.get(
        getattr(business, "industry", None) or "pet_grooming", INDUSTRIES["pet_grooming"]
    )


def can_be_mobile(industry: str | None) -> bool:
    return (industry or "pet_grooming") in MOBILE_INDUSTRIES
