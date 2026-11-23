"""Telling the owner their receptionist is ready.

Two emails, so each says only what's true when it's sent:

- "owner.live" when `provision approve` (or `create`) makes their business: the booking
  page, the staff dashboard and how to sign in, and the calendar. The phone number comes
  later, so it says one is on its way.
- "owner.phone_ready" when a number is first registered for the business: the number, a
  test call, and how to forward their existing number to it.

Both go to the account that signed up, through the same outbox as every other notice, and
replies reach ADMIN_EMAIL. Businesses with no sign-up (loaded from a file) and the demo get
neither.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Business, BusinessProfile, SubscriberAccount
from app.webhooks import enqueue_notice

LIVE = "owner.live"
PHONE_READY = "owner.phone_ready"


def site_link(settings: Settings, slug: str, path: str = "") -> str:
    """The full address when the site's is known, otherwise the path to add to it."""
    return f"{settings.public_site_url.rstrip('/')}/b/{slug}{path}"


def spoken_number(number: str) -> str:
    """+14085550134 as (408) 555-0134; other countries as given."""
    if len(number) == 12 and number.startswith("+1"):
        return f"({number[2:5]}) {number[5:8]}-{number[8:]}"
    return number


def first_name(account: SubscriberAccount) -> str:
    return (account.full_name.split() or ["there"])[0]


def owner_of(session: Session, business: Business) -> SubscriberAccount | None:
    return session.scalar(
        select(SubscriberAccount)
        .join(BusinessProfile, BusinessProfile.account_id == SubscriberAccount.id)
        .where(BusinessProfile.business_id == business.id)
    )


def tell_live(
    session: Session,
    settings: Settings,
    account: SubscriberAccount,
    business: Business,
    calendar_id: str | None,
    now: datetime | None = None,
) -> None:
    enqueue_notice(
        session,
        LIVE,
        {
            "owner_name": first_name(account),
            "owner_email": account.email,
            "business_name": business.name,
            "business_slug": business.slug,
            "booking_url": site_link(settings, business.slug),
            "dashboard_url": site_link(settings, business.slug, "/staff"),
            "calendar_id": calendar_id,
            "trial_ends_at": business.trial_ends_at.isoformat()
            if business.plan == "trial" and business.trial_ends_at
            else None,
        },
        now or datetime.now(UTC),
    )


def tell_phone_ready(
    session: Session,
    settings: Settings,
    business: Business,
    number: str,
    now: datetime | None = None,
) -> bool:
    """Emails the owner their new number. False when there's no owner to tell."""
    account = None if business.is_demo else owner_of(session, business)
    if account is None:
        return False
    enqueue_notice(
        session,
        PHONE_READY,
        {
            "owner_name": first_name(account),
            "owner_email": account.email,
            "business_name": business.name,
            "business_slug": business.slug,
            "number": number,
            "dashboard_url": site_link(settings, business.slug, "/staff"),
        },
        now or datetime.now(UTC),
    )
    return True


def trial_lines(trial_ends_at: str | None) -> list[str]:
    """The free trial, for a business that has one."""
    if not trial_ends_at:
        return []
    from app.plans import MONTHLY_CALLS, PRICE, TRIAL_DAYS

    ends = datetime.fromisoformat(trial_ends_at)
    return [
        f"Your free trial: {TRIAL_DAYS} days, until {ends:%B} {ends.day}. After that it's "
        f"{PRICE} a month for up to {MONTHLY_CALLS} calls. No card is needed and nothing is "
        'charged automatically: to keep your receptionist, press "Keep my receptionist" on '
        "your dashboard and we'll send you an invoice. If you don't, it only takes messages "
        "after the trial.",
        "",
    ]


def compose(event_type: str, data: dict, service_name: str) -> tuple[str, list[str]]:
    business = data.get("business_name") or "your business"
    if event_type == LIVE:
        calendar = data.get("calendar_id")
        return f"{business} is set up with {service_name}", [
            f"Hi {data.get('owner_name')},",
            "",
            f"Your receptionist for {business} is set up.",
            "",
            f"Your booking page: {data.get('booking_url')}",
            "Customers can book there any time. Link to it from your website and your Google "
            "listing.",
            "",
            f"Your staff dashboard: {data.get('dashboard_url')}",
            "Sign in with the email and password you signed up with. Bookings, callback "
            "requests and blocked time are there, and you get an email for each booking.",
            "",
            (
                f"Your calendar: bookings go into your Google Calendar ({calendar}), and times "
                "you're busy there are never offered."
                if calendar
                else "Your calendar: not connected. You can connect your Google Calendar in "
                "your profile at any time."
            ),
            "",
            "Your phone number: we're setting it up now, and we'll email you as soon as your "
            "receptionist answers calls.",
            "",
            *trial_lines(data.get("trial_ends_at")),
            "To change your hours, services or prices, change them in your profile or reply to "
            "this email, and we'll update your receptionist.",
            "",
            service_name,
        ]
    number = spoken_number(str(data.get("number", "")))
    return f"Your receptionist answers at {number}", [
        f"Hi {data.get('owner_name')},",
        "",
        f"Calls to {number} are now answered by your receptionist for {business}. It tells "
        "callers it's an AI assistant, answers questions about your services, prices and "
        "hours, books appointments, and takes a callback request for anything else.",
        "",
        "1. Try it: call the number, book a test appointment, and check that it shows on your "
        f"dashboard ({data.get('dashboard_url')}). Cancel it there afterwards.",
        "",
        "2. Keep your own number: ask your phone provider to forward calls to "
        f"{number}. You can forward every call, or only the ones you don't answer or that "
        "come while you're busy. Your provider can tell you how; it's often a short code or a "
        "setting in their app.",
        "",
        f"3. Give {number} to customers wherever you'd like the receptionist to answer, for "
        "example on your website.",
        "",
        "Questions? Just reply to this email.",
        "",
        service_name,
    ]
