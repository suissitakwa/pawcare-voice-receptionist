"""Turn a subscriber's submitted profile into a live business, after a person reviews it.

Usage:
  python -m app.provision list                        submitted profiles and whether they're live
  python -m app.provision draft <email> > file.json   a business file prefilled from the profile
  python -m app.provision create <email> <file.json>  create the business and the owner's login

The profile has the name, phone, FAQs and calendar choice, but not hours, services or
prices. `draft` fills in what it can; the rest comes from the template and must be
completed with the owner (company/groomer-intake.md) before `create`.

`create` links the profile to the business and gives the owner a staff login with the
password they signed up with, so no password changes hands. It never changes an existing
business: to edit one later, use `python -m app.businesses load <file> --update`.
"""

import json
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.businesses import TEMPLATE_FILE, load_business, read_seed
from app.models import Business, BusinessProfile, SubscriberAccount
from app.schemas import BusinessSeed
from app.staff import upsert_staff
from app.subscriber_schemas import ProfileDetails

# The receptionist talks about dogs and dog sizes. Other types wait for industry packages.
SUPPORTED_TYPES = {"pet_grooming"}


class ProvisionError(ValueError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "business"


def submitted_profile(session: Session, email: str) -> tuple[SubscriberAccount, BusinessProfile]:
    found = session.execute(
        select(SubscriberAccount, BusinessProfile)
        .join(BusinessProfile, BusinessProfile.account_id == SubscriberAccount.id)
        .where(SubscriberAccount.email == email.strip().lower())
    ).first()
    if found is None:
        raise ProvisionError(f"No subscriber with email {email}.")
    account, profile = found
    if profile.status != "submitted":
        raise ProvisionError(f"{email} hasn't submitted their profile yet.")
    if profile.business_type not in SUPPORTED_TYPES:
        raise ProvisionError(
            f"{email} is a {profile.business_type}. The receptionist only handles pet grooming "
            "for now."
        )
    return account, profile


def free_slug(session: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while session.scalar(select(Business.id).where(Business.slug == slug)) is not None:
        slug = f"{base[:60]}-{n}"
        n += 1
    return slug


def draft(session: Session, email: str) -> dict:
    """A business file with the profile's details over the template's examples."""
    account, profile = submitted_profile(session, email)
    if profile.business_id is not None:
        raise ProvisionError(f"{email} is already live.")
    details = ProfileDetails.from_stored(profile.details)
    data = json.loads(TEMPLATE_FILE.read_text())
    faqs = []
    for item in details.faqs:
        slug = slugify(item.question)[:60]
        while slug in {f["slug"] for f in faqs}:
            slug += "-x"
        faqs.append({"slug": slug, "question": item.question, "answer": item.answer})
    data.update(
        slug=free_slug(session, profile.business_name),
        name=profile.business_name,
        tagline="",
        about=f"{profile.business_name} is a pet grooming business.",
        contact_email=account.email,
        # Bookings and callback requests are emailed to the person who signed up.
        notify_email=account.email,
        contact_phone=details.business_phone or "",
        address="",
        is_demo=False,
        faqs=faqs or data["faqs"],
    )
    return data


def follow_ups(details: ProfileDetails) -> list[str]:
    """What still needs a person after the business exists."""
    steps = []
    if details.calendar_provider == "google":
        steps.append(
            "Connect their Google Calendar: python -m app.integrations set-calendar "
            "<slug> <calendar id> <key.json> (docs/calendar.md)."
        )
    elif details.calendar_provider not in (None, "none"):
        steps.append(
            f"They use {details.calendar_provider}, which isn't connected. Agree with them to "
            "keep bookings in Google Calendar during the pilot, or the receptionist's own."
        )
    if details.knowledge_urls:
        steps.append(
            "Their web pages aren't read automatically. Turn what callers ask into FAQs: "
            + ", ".join(details.knowledge_urls)
        )
    steps.append(
        "Buy their Twilio number and register it: python -m app.phone_numbers add <slug> <number>."
    )
    return steps


def create(session: Session, email: str, seed: BusinessSeed) -> tuple[uuid.UUID, list[str]]:
    """Creates the business from a completed file, links the profile and adds the owner's
    login. Returns the business id and the steps left for a person."""
    account, profile = submitted_profile(session, email)
    if profile.business_id is not None:
        raise ProvisionError(f"{email} is already live.")
    if seed.is_demo:
        raise ProvisionError("Set is_demo to false for a real business.")
    business_id = load_business(session, seed)
    profile.business_id = business_id
    # Their sign-up password also opens the staff dashboard.
    upsert_staff(session, seed.slug, account.email, account.full_name, account.password_hash)
    session.flush()
    return business_id, follow_ups(ProfileDetails.from_stored(profile.details))


def main() -> None:
    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    from app.db import get_engine

    try:
        with Session(get_engine()) as session, session.begin():
            if command == "list" and not args:
                rows = session.execute(
                    select(SubscriberAccount.email, BusinessProfile, Business.slug)
                    .join(BusinessProfile, BusinessProfile.account_id == SubscriberAccount.id)
                    .outerjoin(Business, Business.id == BusinessProfile.business_id)
                    .where(BusinessProfile.status == "submitted")
                    .order_by(BusinessProfile.submitted_at)
                ).all()
                for email, profile, slug in rows:
                    state = f"live at /b/{slug}" if slug else "waiting"
                    print(f"{email}  {profile.business_name}  ({profile.business_type})  {state}")
            elif command == "draft" and len(args) == 1:
                print(json.dumps(draft(session, args[0]), indent=2))
                print(
                    "Check timezone, address, hours, groomers, sizes, services and prices with "
                    "the owner before create.",
                    file=sys.stderr,
                )
            elif command == "create" and len(args) == 2:
                seed = read_seed(Path(args[1]))
                _, steps = create(session, args[0], seed)
                print(f"Created {seed.name} at /b/{seed.slug}. {args[0]} can sign in at")
                print(f"/b/{seed.slug}/staff with their account password.")
                print("Still to do:")
                for step in steps:
                    print(f"- {step}")
            else:
                sys.exit(__doc__)
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
