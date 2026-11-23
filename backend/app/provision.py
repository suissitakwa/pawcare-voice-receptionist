"""Turn a subscriber's submitted profile into a live business, after a person reviews it.

Usage:
  python -m app.provision list                        submitted profiles and whether they're live
  python -m app.provision approve <email> [--yes]     show what the profile makes, then create it
  python -m app.provision draft <email> > file.json   the same business as a file, to adjust
  python -m app.provision create <email> <file.json>  create the business from an adjusted file

The profile has the name, phone, hours, services and prices, team, calendar choice and
answers to common questions, so `approve` builds the whole business from it. It refuses a
profile without hours or services (one submitted before those were asked for); use `draft`,
fill them in with the owner (company/groomer-intake.md), then `create`.

Both link the profile to the business and give the owner a staff login with the password
they signed up with, so no password changes hands. Neither changes an existing business: to
edit one later, use `python -m app.businesses load <file> --update`.
"""

import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import go_live, plans
from app.businesses import TEMPLATE_FILE, load_business, read_seed
from app.config import get_settings
from app.industries import STANDARD_SIZE
from app.integrations import share_calendar
from app.models import Business, BusinessProfile, SubscriberAccount
from app.schemas import BusinessSeed
from app.staff import upsert_staff
from app.subscriber_schemas import ProfileDetails, prices_for

# Every kind of business the receptionist has wording and booking rules for.
SUPPORTED_TYPES = {"pet_grooming", "hair_salon", "barbershop"}
# A salon or barbershop books every service as this one size.
STANDARD_SIZES = [
    {
        "slug": STANDARD_SIZE,
        "label": "Standard",
        "description": "",
        "requires_staff_assessment": False,
    }
]
BUSINESS_KINDS = {
    "pet_grooming": "pet grooming",
    "hair_salon": "hair salon",
    "barbershop": "barbershop",
}
# Taken from the template until owners can set them.
BOOKING_RULES = (
    "slot_increment_minutes",
    "buffer_minutes",
    "min_notice_minutes",
    "booking_horizon_days",
)


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
        raise ProvisionError(f"{email} is a {profile.business_type}, which isn't supported.")
    return account, profile


def free_slug(session: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while session.scalar(select(Business.id).where(Business.slug == slug)) is not None:
        slug = f"{base[:60]}-{n}"
        n += 1
    return slug


def unique_slug(name: str, taken: set[str]) -> str:
    base = slugify(name)[:60]
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


def build(session: Session, account: SubscriberAccount, profile: BusinessProfile):
    """A business file built from the profile. Returns it and the sections the profile left
    empty, which are filled from the template's examples and need checking with the owner."""
    details = ProfileDetails.from_stored(profile.details)
    template = json.loads(TEMPLATE_FILE.read_text())
    pets = profile.business_type == "pet_grooming"
    gaps = []

    hours = [
        {
            "weekday": h.weekday,
            "opens_at": h.opens_at.isoformat(),
            "closes_at": h.closes_at.isoformat(),
        }
        for h in details.hours
    ]
    if not hours:
        gaps.append("opening hours")
        hours = template["hours"]

    taken: set[str] = set()
    services = []
    slug_by_name = {}
    for item in details.services:
        prices = prices_for(profile.business_type, item.prices)
        if not prices:
            continue
        slug = unique_slug(item.name, taken)
        slug_by_name[item.name.strip().lower()] = slug
        services.append(
            {
                "slug": slug,
                "name": item.name.strip(),
                "description": item.description.strip(),
                "options": [
                    {
                        # Businesses without dog sizes book every service as one size.
                        "size": price.size if pets else STANDARD_SIZE,
                        "duration_minutes": price.duration_minutes,
                        "price_cents": price.price_cents,
                    }
                    for price in prices
                ],
            }
        )
    if not services:
        gaps.append("services and prices")
        services = template["services"]
        slug_by_name = {s["name"].lower(): s["slug"] for s in services}

    team = details.team or []
    people = team or [_owner_as_team(account)]
    staff_slugs: set[str] = set()
    groomers = [
        {
            "slug": unique_slug(person.name, staff_slugs),
            "display_name": person.name.strip(),
            # Everyone works the opening hours until per-person hours exist.
            "hours": [
                {"weekday": h["weekday"], "starts_at": h["opens_at"], "ends_at": h["closes_at"]}
                for h in hours
            ],
            "breaks": [],
            "services": [
                slug_by_name[name.strip().lower()]
                for name in person.services
                if name.strip().lower() in slug_by_name
            ],
        }
        for person in people
    ]

    faq_slugs: set[str] = set()
    faqs = [
        {"slug": unique_slug(f.question, faq_slugs), "question": f.question, "answer": f.answer}
        for f in details.faqs
    ]
    kind = BUSINESS_KINDS.get(profile.business_type, "grooming")
    data = {
        **{key: template[key] for key in BOOKING_RULES},
        "slug": free_slug(session, profile.business_name),
        "name": profile.business_name,
        "tagline": "",
        "about": f"{profile.business_name} is a {kind} business.",
        "timezone": details.timezone or "America/Los_Angeles",
        "contact_email": account.email,
        # Bookings and callback requests are emailed to the person who signed up.
        "notify_email": account.email,
        "contact_phone": details.business_phone or "",
        "address": details.address or "",
        "is_demo": False,
        "industry": profile.business_type,
        # Only groomers can be mobile; a salon's or barbershop's leftover setting is ignored.
        "is_mobile": pets and details.is_mobile,
        "service_zip_codes": details.service_zip_codes if pets and details.is_mobile else [],
        "service_area": (details.service_area or "") if pets and details.is_mobile else "",
        "hours": hours,
        "groomers": groomers,
        "size_categories": template["size_categories"] if pets else STANDARD_SIZES,
        "services": services,
        "faqs": faqs or template["faqs"],
        "voice": details.voice,
    }
    if not details.timezone:
        gaps.append("time zone (Pacific assumed)")
    return data, gaps


def _owner_as_team(account: SubscriberAccount):
    from app.subscriber_schemas import TeamMember

    return TeamMember(name=(account.full_name.split() or ["Owner"])[0])


def draft(session: Session, email: str) -> dict:
    """The business file the profile would make, to review or adjust before create."""
    account, profile = submitted_profile(session, email)
    if profile.business_id is not None:
        raise ProvisionError(f"{email} is already live.")
    return build(session, account, profile)[0]


def approve(session: Session, email: str):
    """Creates the business straight from the profile. Refuses a profile that leaves hours or
    services empty, rather than going live with the template's example prices."""
    account, profile = submitted_profile(session, email)
    if profile.business_id is not None:
        raise ProvisionError(f"{email} is already live.")
    data, gaps = build(session, account, profile)
    blocking = [gap for gap in gaps if not gap.startswith("time zone")]
    if blocking:
        raise ProvisionError(
            f"The profile has no {' or '.join(blocking)}. Ask the owner to add them, or use "
            "draft and create to fill them in by hand."
        )
    seed = BusinessSeed.model_validate(data)
    business_id, steps = create(session, email, seed)
    return seed, gaps, steps


def summary(data: dict) -> str:
    """What approve would create, for a person to check before saying yes."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = [
        f"{data['name']}  ->  /b/{data['slug']}  ({data['timezone']})",
        "Hours: "
        + ", ".join(
            f"{days[h['weekday']]} {h['opens_at'][:5]}-{h['closes_at'][:5]}" for h in data["hours"]
        ),
        "Services:",
    ]
    for service in data["services"]:
        prices = ", ".join(
            f"{o['size']} {o['duration_minutes']} min ${o['price_cents'] / 100:.2f}"
            for o in service["options"]
        )
        lines.append(f"  {service['name']}: {prices}")
    lines.append("Team: " + ", ".join(g["display_name"] for g in data["groomers"]))
    lines.append(f"Answers to common questions: {len(data['faqs'])}")
    return "\n".join(lines)


def follow_ups(details: ProfileDetails) -> list[str]:
    """What still needs a person after the business exists."""
    steps = []
    if details.google_calendar_connected:
        pass  # Connected from their profile; approve connects the business to it.
    elif details.calendar_provider == "google":
        steps.append(
            "Ask them to connect their Google Calendar in their profile (Your calendar step). "
            "Once they've shared it: python -m app.integrations share-calendar <slug> "
            "<calendar id> (docs/calendar.md)."
        )
    elif details.calendar_provider not in (None, "none"):
        steps.append(
            f"They use {details.calendar_provider}, which isn't connected. Agree with them to "
            "keep bookings in Google Calendar during the pilot, or the receptionist's own."
        )
    if details.knowledge_urls:
        # Pages are never read on calls; only the answers the owner added from them are.
        steps.append(
            f"The receptionist knows only their {len(details.faqs)} answers to common "
            "questions, not the pages themselves. If something callers ask is missing, ask "
            "them to use Suggest answers in their profile (docs/knowledge.md): "
            + ", ".join(details.knowledge_urls)
        )
    steps.append(
        "Buy their Twilio number and register it: python -m app.phone_numbers add <slug> <number>. "
        "That emails them the number and how to forward theirs to it."
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
    # A business from a sign-up starts its free trial when it goes live.
    plans.start_trial(session.get(Business, business_id), datetime.now(UTC))
    details = ProfileDetails.from_stored(profile.details)
    if details.google_calendar_connected and details.google_calendar_id:
        # They connected it while signing up, so bookings go to it from the start.
        try:
            share_calendar(session, get_settings(), seed.slug, details.google_calendar_id)
        except ValueError as error:
            raise ProvisionError(f"Couldn't connect their calendar: {error}") from None
    # Their sign-up password also opens the staff dashboard.
    upsert_staff(session, seed.slug, account.email, account.full_name, account.password_hash)
    session.flush()
    connected = details.google_calendar_id if details.google_calendar_connected else None
    go_live.tell_live(
        session, get_settings(), account, session.get(Business, business_id), connected
    )
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
            elif command == "approve" and args[:1] and args[1:] in ([], ["--yes"]):
                account, profile = submitted_profile(session, args[0])
                data, gaps = build(session, account, profile)
                print(summary(data))
                for gap in gaps:
                    print(f"Note: no {gap} in the profile.")
                if args[1:] != ["--yes"] and input("Create this business? [y/N] ").strip() != "y":
                    sys.exit("Nothing created.")
                seed, _, steps = approve(session, args[0])
                print(f"Created {seed.name} at /b/{seed.slug}. {args[0]} can sign in at")
                print(f"/b/{seed.slug}/staff with their account password. They're emailed")
                print("their booking page and dashboard now, and their number once it's added.")
                print("Still to do:")
                for step in steps:
                    print(f"- {step}")
            elif command == "draft" and len(args) == 1:
                print(json.dumps(draft(session, args[0]), indent=2))
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
