"""Which business a phone call is for, from the number the caller dialed.

Rules, for OpenAI and Vapi calls alike:

1. A number registered to a business is answered for that business.
2. Otherwise the call goes to the deployment's own business (BUSINESS_SLUG) when the dialed
   number is its PHONE_NUMBER, when the dialed number can't be read, or while no number is
   registered at all. That keeps a single-business deployment working unchanged.
3. Anything else is declined.

Usage:
  python -m app.phone_numbers list
  python -m app.phone_numbers add <business-slug> <number>
  python -m app.phone_numbers remove <number>
"""

import logging
import re
import sys

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app import go_live
from app.config import Settings, get_settings
from app.models import Business, BusinessPhoneNumber

logger = logging.getLogger("pawcare.phone")

E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
SIP_NUMBER = re.compile(r"sip:(\+?[0-9]{7,15})@")


def normalize(raw: object) -> str | None:
    """A number in E.164, or None. US numbers without a country code get +1."""
    if not isinstance(raw, str):
        return None
    digits = re.sub(r"[\s().-]", "", raw)
    if not digits.startswith("+"):
        # Without a country code, only a US number is unambiguous.
        if len(digits) == 10:
            digits = f"+1{digits}"
        elif len(digits) == 11 and digits.startswith("1"):
            digits = f"+{digits}"
        else:
            return None
    return digits if E164.match(digits) else None


def dialed_from_sip(sip_headers: object) -> str | None:
    """The dialed number from the SIP To header of an OpenAI incoming-call event."""
    for header in sip_headers if isinstance(sip_headers, list) else []:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "to":
            match = SIP_NUMBER.search(str(header.get("value", "")))
            return normalize(match.group(1)) if match else None
    return None


def dialed_from_vapi(message: dict) -> str | None:
    """The dialed number from a Vapi server message: its phone number object."""
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    for holder in (message, call):
        phone = holder.get("phoneNumber")
        if isinstance(phone, dict) and normalize(phone.get("number")):
            return normalize(phone.get("number"))
    return None


def business_for_call(session: Session, settings: Settings, dialed: str | None) -> Business | None:
    if dialed is not None:
        business = session.scalar(
            select(Business)
            .join(BusinessPhoneNumber, BusinessPhoneNumber.business_id == Business.id)
            .where(BusinessPhoneNumber.number == dialed)
        )
        if business is not None:
            return business
    any_registered = session.scalar(select(exists().where(BusinessPhoneNumber.id.is_not(None))))
    if dialed is None or dialed == normalize(settings.phone_number) or not any_registered:
        if dialed is None:
            logger.warning("Could not read the dialed number; using the deployment's business")
        return session.scalar(select(Business).where(Business.slug == settings.business_slug))
    logger.warning("Call to a number no business has registered")
    return None


def public_number(session: Session, settings: Settings, business: Business) -> str | None:
    """The number to show on a business's site: its first registered number, or for the
    deployment's own business, PHONE_NUMBER."""
    registered = session.scalar(
        select(BusinessPhoneNumber.number)
        .where(BusinessPhoneNumber.business_id == business.id)
        .order_by(BusinessPhoneNumber.created_at, BusinessPhoneNumber.number)
        .limit(1)
    )
    if registered is not None:
        return registered
    if business.slug == settings.business_slug:
        return normalize(settings.phone_number)
    return None


def add_number(session: Session, business_slug: str, raw_number: str) -> str:
    number = normalize(raw_number)
    if number is None:
        raise ValueError(f"{raw_number} is not a phone number. Use E.164, e.g. +13235550100.")
    business = session.scalar(select(Business).where(Business.slug == business_slug))
    if business is None:
        raise ValueError(f"No business with slug {business_slug}.")
    business_id = business.id
    owner = session.scalar(
        select(Business.slug)
        .join(BusinessPhoneNumber, BusinessPhoneNumber.business_id == Business.id)
        .where(BusinessPhoneNumber.number == number)
    )
    if owner is not None and owner != business_slug:
        raise ValueError(f"{number} already belongs to {owner}. Remove it there first.")
    if owner is None:
        first = not session.scalar(
            select(exists().where(BusinessPhoneNumber.business_id == business_id))
        )
        session.add(BusinessPhoneNumber(business_id=business_id, number=number))
        session.flush()
        if first:
            # Its owner hears once, with the number and how to forward theirs to it.
            go_live.tell_phone_ready(session, get_settings(), business, number)
    return number


def remove_number(session: Session, raw_number: str) -> bool:
    number = normalize(raw_number)
    if number is None:
        return False
    removed = session.execute(
        delete(BusinessPhoneNumber).where(BusinessPhoneNumber.number == number)
    )
    return removed.rowcount > 0


def main() -> None:
    from app.db import get_engine

    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    with Session(get_engine()) as session, session.begin():
        try:
            if command == "list" and not args:
                rows = session.execute(
                    select(Business.slug, BusinessPhoneNumber.number)
                    .join(BusinessPhoneNumber, BusinessPhoneNumber.business_id == Business.id)
                    .order_by(Business.slug, BusinessPhoneNumber.number)
                ).all()
                for slug, number in rows:
                    print(f"{number}  {slug}")
                if not rows:
                    print("No numbers registered.")
            elif command == "add" and len(args) == 2:
                print(f"{add_number(session, args[0], args[1])} now rings {args[0]}.")
                print("Its owner is emailed the number (only for the business's first number).")
            elif command == "remove" and len(args) == 1:
                print("Removed." if remove_number(session, args[0]) else "No such number.")
            else:
                sys.exit(__doc__)
        except ValueError as exc:
            sys.exit(str(exc))


if __name__ == "__main__":
    main()
