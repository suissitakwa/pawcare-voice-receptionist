"""Create or update a business from a JSON file, in the same format as the demo seed.

Usage:
  python -m app.businesses template > my-business.json   start from a filled-in example
  python -m app.businesses check <file.json>             validate without saving
  python -m app.businesses load <file.json>              create a new business
  python -m app.businesses load <file.json> --update     replace an existing one's details
  python -m app.businesses list

Loading again with --update replaces the business's details, hours, FAQs and prices.
Services missing from the file are switched off rather than deleted, so past bookings keep
their service. Bookings, staff, phone numbers and integrations are never touched.
"""

import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business
from app.schemas import EMAIL_PATTERN, SLUG_PATTERN, BusinessSeed
from app.seed import seed_business

TEMPLATE_FILE = Path(__file__).parent / "seed_data" / "business_template.json"


def problems(seed: BusinessSeed) -> list[str]:
    """What stops this file from becoming a working business. Empty when it's fine."""
    found = []
    if not re.match(SLUG_PATTERN, seed.slug):
        found.append(
            f"slug {seed.slug!r} must be lowercase letters, digits and hyphens (it becomes the "
            "web address)"
        )
    if not seed.name.strip():
        found.append("name is empty")
    if not EMAIL_PATTERN.match(seed.contact_email):
        found.append(f"contact_email {seed.contact_email!r} is not an email address")
    if not seed.services:
        found.append("there are no services")
    if not seed.groomers:
        found.append("there is no groomer, so no time could be booked")
    if not seed.hours:
        found.append("there are no opening hours")
    for label, slugs in (
        ("service", [service.slug for service in seed.services]),
        ("size", [size.slug for size in seed.size_categories]),
        ("FAQ", [faq.slug for faq in seed.faqs]),
        ("groomer", [groomer.slug for groomer in seed.groomers]),
    ):
        for slug, count in Counter(slugs).items():
            if count > 1:
                found.append(f"{label} slug {slug!r} is used {count} times")
    for day, count in Counter(hours.weekday for hours in seed.hours).items():
        if count > 1:
            found.append(f"weekday {day} has {count} opening hours; give one per day")
    for hours in seed.hours:
        if hours.closes_at <= hours.opens_at:
            found.append(f"weekday {hours.weekday} closes before it opens")
    for groomer in seed.groomers:
        for period in [*groomer.hours, *groomer.breaks]:
            if period.ends_at <= period.starts_at:
                found.append(f"{groomer.slug}: a period on weekday {period.weekday} ends early")
    for service in seed.services:
        if not service.options:
            found.append(f"{service.slug} has no size with a price and duration")
    return found


def read_seed(path: Path) -> BusinessSeed:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"Can't read {path}: {error}") from None
    try:
        seed = BusinessSeed.model_validate(raw)
    except ValidationError as error:
        lines = [
            f"{'.'.join(str(part) for part in issue['loc']) or 'file'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise ValueError("The file has problems:\n- " + "\n- ".join(lines)) from None
    found = problems(seed)
    if found:
        raise ValueError("The file has problems:\n- " + "\n- ".join(found))
    return seed


def load_business(session: Session, seed: BusinessSeed, update: bool = False) -> uuid.UUID:
    """Creates the business, or with update=True replaces an existing one's details."""
    found = problems(seed)
    if found:
        raise ValueError("The file has problems:\n- " + "\n- ".join(found))
    exists = session.scalar(select(Business.id).where(Business.slug == seed.slug))
    if exists is not None and not update:
        raise ValueError(
            f"A business with slug {seed.slug} already exists. Use --update to replace its details."
        )
    if exists is None and update:
        raise ValueError(f"No business with slug {seed.slug} to update. Leave out --update.")
    return seed_business(session, seed)


def main() -> None:
    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if command == "template" and not args:
        print(TEMPLATE_FILE.read_text(), end="")
        return
    try:
        if command == "check" and len(args) == 1:
            seed = read_seed(Path(args[0]))
            print(f"{seed.slug}: looks good ({len(seed.services)} services).")
            return
        from app.db import get_engine

        with Session(get_engine()) as session, session.begin():
            if command == "list" and not args:
                rows = session.execute(
                    select(Business.slug, Business.name, Business.is_demo).order_by(Business.slug)
                ).all()
                for slug, name, is_demo in rows:
                    print(f"{slug}  {name}{'  (demo)' if is_demo else ''}")
            elif command == "load" and len(args) in (1, 2) and args[1:] in ([], ["--update"]):
                seed = read_seed(Path(args[0]))
                load_business(session, seed, update=args[1:] == ["--update"])
                verb = "Updated" if args[1:] else "Created"
                print(f"{verb} {seed.name}. Its site is /b/{seed.slug}.")
            else:
                sys.exit(__doc__)
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
