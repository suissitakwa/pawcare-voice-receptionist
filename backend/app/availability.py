"""Works out which appointment start times are genuinely open on one date.

`compute_slots` is pure so that time zone and overlap rules can be tested exactly.
All instants are compared in UTC, because local wall-clock times repeat or vanish
when daylight saving changes.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BlockedPeriod, Booking, Business, Groomer, GroomerBreak, GroomerHours


@dataclass(frozen=True)
class Interval:
    """A half-open range, so an appointment may start exactly when another ends."""

    start: datetime
    end: datetime

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class DayRules:
    timezone: ZoneInfo
    business_hours: tuple[time, time] | None
    groomer_hours: tuple[time, time] | None
    breaks: tuple[tuple[time, time], ...]
    blocked: tuple[Interval, ...]
    booked: tuple[Interval, ...]
    increment: timedelta
    buffer: timedelta
    min_notice: timedelta


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime


def local_instant(day: date, wall_clock: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, wall_clock, tzinfo=timezone).astimezone(UTC)


def working_window(day: date, rules: DayRules) -> Interval | None:
    """The part of the date when the business is open and the groomer is working."""
    if rules.business_hours is None or rules.groomer_hours is None:
        return None
    start = max(rules.business_hours[0], rules.groomer_hours[0])
    end = min(rules.business_hours[1], rules.groomer_hours[1])
    window = Interval(
        local_instant(day, start, rules.timezone), local_instant(day, end, rules.timezone)
    )
    return window if window.start < window.end else None


def compute_slots(day: date, duration: timedelta, rules: DayRules, now: datetime) -> list[Slot]:
    """Starts where the appointment fits and its trailing buffer clears later bookings.

    The buffer protects the next appointment only, so it may run past closing time
    or into a break, but the groomer must be free for the appointment itself.
    """
    window = working_window(day, rules)
    if window is None:
        return []

    unavailable = [
        Interval(local_instant(day, start, rules.timezone), local_instant(day, end, rules.timezone))
        for start, end in rules.breaks
    ]
    unavailable.extend(rules.blocked)
    earliest = now + rules.min_notice

    slots = []
    start = window.start
    while start + duration <= window.end:
        appointment = Interval(start, start + duration)
        occupied = Interval(start, appointment.end + rules.buffer)
        if (
            start >= earliest
            and not any(appointment.overlaps(other) for other in unavailable)
            and not any(occupied.overlaps(other) for other in rules.booked)
        ):
            slots.append(Slot(appointment.start, appointment.end))
        start += rules.increment
    return slots


def active_groomer_id(session: Session, business_id: uuid.UUID) -> uuid.UUID | None:
    # The first release has one groomer, so a business's calendar is that groomer's.
    return session.scalar(
        select(Groomer.id)
        .where(Groomer.business_id == business_id, Groomer.is_active)
        .order_by(Groomer.slug)
        .limit(1)
    )


def load_day_rules(session: Session, business: Business, day: date) -> DayRules:
    timezone = ZoneInfo(business.timezone)
    weekday = day.weekday()
    business_hours = next(
        ((h.opens_at, h.closes_at) for h in business.hours if h.weekday == weekday), None
    )
    groomer_id = active_groomer_id(session, business.id)
    groomer_hours = None
    breaks: tuple[tuple[time, time], ...] = ()
    blocked: tuple[Interval, ...] = ()
    booked: tuple[Interval, ...] = ()

    if groomer_id is not None:
        row = session.execute(
            select(GroomerHours.starts_at, GroomerHours.ends_at).where(
                GroomerHours.groomer_id == groomer_id, GroomerHours.weekday == weekday
            )
        ).first()
        groomer_hours = (row[0], row[1]) if row else None
        breaks = tuple(
            (start, end)
            for start, end in session.execute(
                select(GroomerBreak.starts_at, GroomerBreak.ends_at).where(
                    GroomerBreak.groomer_id == groomer_id, GroomerBreak.weekday == weekday
                )
            )
        )
        # Any instant that belongs to this local date falls inside this UTC range.
        range_start = local_instant(day, time.min, timezone)
        range_end = local_instant(day + timedelta(days=1), time.min, timezone)
        blocked = tuple(
            Interval(start, end)
            for start, end in session.execute(
                select(BlockedPeriod.starts_at, BlockedPeriod.ends_at).where(
                    BlockedPeriod.groomer_id == groomer_id,
                    BlockedPeriod.starts_at < range_end,
                    BlockedPeriod.ends_at > range_start,
                )
            )
        )
        booked = tuple(
            Interval(start, end)
            for start, end in session.execute(
                select(Booking.starts_at, Booking.occupied_until).where(
                    Booking.groomer_id == groomer_id,
                    Booking.status == "confirmed",
                    Booking.starts_at < range_end,
                    Booking.occupied_until > range_start,
                )
            )
        )

    return DayRules(
        timezone=timezone,
        business_hours=business_hours,
        groomer_hours=groomer_hours,
        breaks=breaks,
        blocked=blocked,
        booked=booked,
        increment=timedelta(minutes=business.slot_increment_minutes),
        buffer=timedelta(minutes=business.buffer_minutes),
        min_notice=timedelta(minutes=business.min_notice_minutes),
    )
