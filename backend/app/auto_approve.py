"""Sign-ups go live on their own while AUTO_APPROVE_SIGNUPS is on, for trying onboarding end
to end.

When a profile is first submitted, it's approved exactly as `provision approve --yes` would,
and the phone number the owner gave at sign-up is registered to ring its receptionist. Its
owner gets the usual emails: set up, then the number.

For testing, owners give the demo line (+12696992551), which rings one business at a time:
the newest sign-up takes it. A number can be taken from the demo or from an earlier
sign-up, never from a business whose number you registered with `phone_numbers add`.

Turn it off before real customers sign up: their own number isn't on our phone line, so
registering it doesn't make calls reach the receptionist (each customer will get their own
Twilio number instead).
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Business, BusinessPhoneNumber, SubscriberAccount
from app.phone_numbers import add_number, normalize
from app.subscriber_schemas import ProfileDetails

logger = logging.getLogger("pawcare.onboarding")


def take_number(session: Session, slug: str, raw: str | None) -> str | None:
    """Registers the sign-up's number for the business, moving it from the demo or an
    earlier sign-up. Returns the number, or None when it can't be used."""
    number = normalize(raw)
    if number is None:
        return None
    holder = session.execute(
        select(BusinessPhoneNumber, Business.is_demo)
        .join(Business, Business.id == BusinessPhoneNumber.business_id)
        .where(BusinessPhoneNumber.number == number)
    ).first()
    if holder is not None:
        line, is_demo = holder
        if not (is_demo or line.from_signup):
            logger.warning("Sign-up number %s belongs to a business; not moved", number)
            return None
        session.execute(delete(BusinessPhoneNumber).where(BusinessPhoneNumber.id == line.id))
        session.flush()
    add_number(session, slug, number)
    session.execute(
        BusinessPhoneNumber.__table__.update()
        .where(BusinessPhoneNumber.number == number)
        .values(from_signup=True)
    )
    return number


def approve_signup(session: Session, account: SubscriberAccount) -> str | None:
    """Approves a just-submitted profile and registers its number. Returns the business's
    slug, or None when it can't be approved (nothing is changed then)."""
    from app.provision import ProvisionError, approve, submitted_profile

    try:
        with session.begin_nested():
            _, profile = submitted_profile(session, account.email)
            phone = ProfileDetails.from_stored(profile.details).business_phone
            seed, _, _ = approve(session, account.email)
            number = take_number(session, seed.slug, phone)
    except (ProvisionError, ValueError) as error:
        logger.warning("Sign-up %s not approved automatically: %s", account.email, error)
        return None
    logger.info("Sign-up approved as %s, number %s", seed.slug, number or "none")
    return seed.slug
