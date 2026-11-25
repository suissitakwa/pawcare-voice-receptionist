"""How a service's price is meant, and how to say it.

Most prices are simply the price. A home services business usually charges a fee to come out
and quotes any repair on site, or gives a starting price. Every place a price is shown or
said (the receptionist, the booking form, emails, the calendar) uses these words, so nobody is
told a visit fee is the cost of the repair.
"""

from typing import Literal

PriceKind = Literal["fixed", "from", "visit_fee"]
PRICE_KINDS: tuple[str, ...] = ("fixed", "from", "visit_fee")
PRICE_KIND_LABELS = {
    "fixed": "Fixed price",
    "from": "Starting price",
    "visit_fee": "Visit fee (repairs quoted on site)",
}


def dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


def price_text(cents: int, kind: str | None) -> str:
    """For people reading: "$89.00 visit fee", "from $150.00" or "$45.00"."""
    if kind == "visit_fee":
        return f"{dollars(cents)} visit fee"
    if kind == "from":
        return f"from {dollars(cents)}"
    return dollars(cents)


def price_note(kind: str | None) -> str | None:
    """What the receptionist must add when it says the price, if anything."""
    if kind == "visit_fee":
        return (
            "This is the fee for the technician to come out. Any repair is quoted on site, so "
            "never say what the repair will cost."
        )
    if kind == "from":
        return "This is the lowest it costs; the final price depends on the job."
    return None
