"""What a business pays for: a free trial, then one plan.

- **trial**: free for TRIAL_DAYS from going live (`provision approve`), up to MONTHLY_CALLS
  phone calls a month. No card; nothing is charged automatically.
- **starter**: PRICE a month, up to MONTHLY_CALLS phone calls a month. Paid by card through
  Stripe (app/billing.py), or invoiced by hand: the owner asks from their dashboard, and you
  mark them paid here.
- **unlimited**: no trial and no allowance, as before: the demo, pilots and every business
  that existed before plans.

When a trial has ended, a card subscription has stopped (cancelled, or unpaid after Stripe's
retries), or this month's calls are used up, the receptionist still answers
but only takes a message (no times, prices or bookings), and the website's voice assistant
is paused. The booking form keeps working: it costs nothing to run.

Usage:
  python -m app.plans show <slug>
  python -m app.plans set <slug> starter|unlimited
  python -m app.plans set <slug> trial [<days>]     restart or extend a trial
"""

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Business, VoiceSession

PRICE = "$49"
MONTHLY_CALLS = 100
TRIAL_DAYS = 14
PLANS = ("trial", "starter", "unlimited")

TRIAL_ENDED = "trial ended"
ALLOWANCE_USED = "monthly call allowance used"
PAYMENT_STOPPED = "plan payment stopped"
# Stripe subscription statuses after which a paid plan no longer works. past_due still works
# while Stripe retries the card.
STOPPED_BILLING = ("canceled", "unpaid", "incomplete_expired")


@dataclass(frozen=True)
class PlanStatus:
    plan: str
    # trial, trial_ended, active, payment_stopped, allowance_used, or unlimited.
    state: str
    price: str
    calls_included: int | None
    calls_this_month: int
    trial_ends_at: datetime | None
    upgrade_requested_at: datetime | None
    # The card subscription's status in Stripe; None when invoiced by hand or never paid.
    billing_status: str | None
    # When a card subscription the owner cancelled stops; None while it renews.
    billing_ends_at: datetime | None


def month_start(now: datetime, timezone: ZoneInfo) -> datetime:
    today = now.astimezone(timezone).date()
    return datetime.combine(date(today.year, today.month, 1), time.min, tzinfo=timezone)


def calls_this_month(session: Session, business: Business, now: datetime) -> int:
    """Phone calls answered for the business since the 1st, in its own time zone."""
    return session.scalar(
        select(func.count())
        .select_from(VoiceSession)
        .where(
            VoiceSession.business_id == business.id,
            VoiceSession.channel == "phone",
            VoiceSession.started_at >= month_start(now, ZoneInfo(business.timezone)),
        )
    )


def plan_problem(session: Session, business: Business, now: datetime) -> str | None:
    """Why the receptionist may only take messages, or None when it works in full."""
    if business.plan == "unlimited":
        return None
    if business.plan == "trial" and business.trial_ends_at and now >= business.trial_ends_at:
        return TRIAL_ENDED
    if business.plan == "starter" and business.billing_status in STOPPED_BILLING:
        return PAYMENT_STOPPED
    if calls_this_month(session, business, now) >= MONTHLY_CALLS:
        return ALLOWANCE_USED
    return None


def status(session: Session, business: Business, now: datetime) -> PlanStatus:
    unlimited = business.plan == "unlimited"
    problem = plan_problem(session, business, now)
    if unlimited:
        state = "unlimited"
    elif problem == TRIAL_ENDED:
        state = "trial_ended"
    elif problem == PAYMENT_STOPPED:
        state = "payment_stopped"
    elif problem == ALLOWANCE_USED:
        state = "allowance_used"
    else:
        state = "trial" if business.plan == "trial" else "active"
    return PlanStatus(
        plan=business.plan,
        state=state,
        price=PRICE,
        calls_included=None if unlimited else MONTHLY_CALLS,
        calls_this_month=calls_this_month(session, business, now),
        trial_ends_at=business.trial_ends_at if business.plan == "trial" else None,
        upgrade_requested_at=business.upgrade_requested_at,
        billing_status=business.billing_status,
        billing_ends_at=business.billing_ends_at if business.plan == "starter" else None,
    )


def start_trial(business: Business, now: datetime, days: int = TRIAL_DAYS) -> None:
    business.plan = "trial"
    business.trial_ends_at = now + timedelta(days=days)


def set_plan(session: Session, slug: str, plan: str, now: datetime, days: int | None = None):
    business = session.scalar(select(Business).where(Business.slug == slug))
    if business is None:
        raise ValueError(f"No business with slug {slug}.")
    if plan not in PLANS:
        raise ValueError(f"Choose one of: {', '.join(PLANS)}.")
    if plan == "trial":
        start_trial(business, now, days if days is not None else TRIAL_DAYS)
    else:
        business.plan = plan
        business.trial_ends_at = None
        # Paid or exempt: the request is answered, and a stopped card subscription no longer
        # holds the plan back. Stripe's IDs stay, so the owner can still reach their invoices.
        business.upgrade_requested_at = None
        business.billing_status = None
        business.billing_ends_at = None
    return business


def describe(session: Session, business: Business, now: datetime) -> str:
    s = status(session, business, now)
    lines = [f"{business.name} ({business.slug}): {s.plan}, {s.state}"]
    if s.trial_ends_at:
        lines.append(f"Trial ends {s.trial_ends_at:%Y-%m-%d %H:%M %Z}")
    if s.calls_included is not None:
        lines.append(f"Calls this month: {s.calls_this_month} of {s.calls_included}")
    if s.billing_status:
        lines.append(f"Card subscription: {s.billing_status}")
    if s.billing_ends_at:
        lines.append(f"Cancelled: ends {s.billing_ends_at:%Y-%m-%d}")
    if s.upgrade_requested_at:
        lines.append(f"Asked to keep it on {s.upgrade_requested_at:%Y-%m-%d}")
    return "\n".join(lines)


def main() -> None:
    from app.db import get_engine

    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    now = datetime.now(UTC)
    with Session(get_engine()) as session, session.begin():
        try:
            if command == "show" and len(args) == 1:
                business = session.scalar(select(Business).where(Business.slug == args[0]))
                if business is None:
                    raise ValueError(f"No business with slug {args[0]}.")
                print(describe(session, business, now))
            elif command == "set" and len(args) in (2, 3):
                days = int(args[2]) if len(args) == 3 else None
                if days is not None and args[1] != "trial":
                    raise ValueError("Only a trial takes a number of days.")
                business = set_plan(session, args[0], args[1], now, days)
                session.flush()
                print(describe(session, business, now))
            else:
                sys.exit(__doc__)
        except ValueError as error:
            sys.exit(str(error))


if __name__ == "__main__":
    main()
