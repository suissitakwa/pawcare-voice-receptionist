"""Works out which appointment start times are genuinely open on one date.

`compute_slots` is pure so that time zone and overlap rules can be tested exactly.
All instants are compared in UTC, because local wall-clock times repeat or vanish
when daylight saving changes.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.errors import ApiError
from app.google_calendar import busy_between
from app.models import (
    BlockedPeriod,
    Booking,
    Business,
    Groomer,
    GroomerBreak,
    GroomerHours,
    GroomerService,
    Service,
    ServiceSizeRule,
    SizeCategory,
)


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
    """The part of the date when the business is open and the staff member is working."""
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


@dataclass(frozen=True)
class StaffMember:
    id: uuid.UUID
    slug: str
    name: str


@dataclass(frozen=True)
class StaffSlot:
    starts_at: datetime
    ends_at: datetime
    # Who is free for this start, the one booked first when the customer has no preference.
    staff: tuple[StaffMember, ...]


@dataclass(frozen=True)
class DayOpenings:
    closed: bool
    slots: list[StaffSlot]


def active_staff(session: Session, business_id: uuid.UUID) -> list[StaffMember]:
    return [
        StaffMember(row.id, row.slug, row.display_name)
        for row in session.execute(
            select(Groomer.id, Groomer.slug, Groomer.display_name)
            .where(Groomer.business_id == business_id, Groomer.is_active)
            .order_by(Groomer.slug)
        )
    ]


def staff_services(session: Session, business_id: uuid.UUID) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The services each staff member is limited to. Anyone missing does every service."""
    limited: dict[uuid.UUID, set[uuid.UUID]] = {}
    for groomer_id, service_id in session.execute(
        select(GroomerService.groomer_id, GroomerService.service_id)
        .join(Groomer, Groomer.id == GroomerService.groomer_id)
        .where(Groomer.business_id == business_id)
    ):
        limited.setdefault(groomer_id, set()).add(service_id)
    return limited


def does_service(limits: dict[uuid.UUID, set[uuid.UUID]], member_id, service_id) -> bool:
    return member_id not in limits or service_id in limits[member_id]


def staff_for(
    session: Session, business_id: uuid.UUID, service_id: uuid.UUID, requested: str | None = None
) -> list[StaffMember]:
    """Who can do this service: everyone qualified, or only the person the customer asked for."""
    limits = staff_services(session, business_id)
    staff = active_staff(session, business_id)
    if requested is None:
        return [m for m in staff if does_service(limits, m.id, service_id)]
    member = next((m for m in staff if m.slug == requested), None)
    if member is None:
        raise ApiError(404, "unknown_staff", "That staff member is not recognised.")
    if not does_service(limits, member.id, service_id):
        raise ApiError(
            422,
            "staff_not_qualified",
            f"{member.name} doesn't do that service. Choose someone else.",
        )
    return [member]


def load_day_rules(
    session: Session,
    business: Business,
    day: date,
    groomer_id: uuid.UUID | None,
    ignore_booking: uuid.UUID | None = None,
) -> DayRules:
    """One staff member's day. `ignore_booking` leaves one booking out of the busy times, so
    it can be moved."""
    timezone = ZoneInfo(business.timezone)
    weekday = day.weekday()
    business_hours = next(
        ((h.opens_at, h.closes_at) for h in business.hours if h.weekday == weekday), None
    )
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
                    BlockedPeriod.business_id == business.id,
                    # This person's own blocks and blocks for the whole business.
                    or_(BlockedPeriod.groomer_id == groomer_id, BlockedPeriod.groomer_id.is_(None)),
                    BlockedPeriod.starts_at < range_end,
                    BlockedPeriod.ends_at > range_start,
                )
            )
        )
        # Busy times in the owner's Google Calendar, if one is connected.
        blocked += tuple(
            Interval(busy.start, busy.end)
            for busy in busy_between(session, business, range_start, range_end)
        )
        booked = tuple(
            Interval(start, end)
            for start, end in session.execute(
                select(Booking.starts_at, Booking.occupied_until).where(
                    Booking.groomer_id == groomer_id,
                    Booking.status == "confirmed",
                    Booking.starts_at < range_end,
                    Booking.occupied_until > range_start,
                    Booking.id != ignore_booking if ignore_booking else true(),
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


def load_business(session: Session, business_id: uuid.UUID) -> Business:
    return session.scalar(
        select(Business).where(Business.id == business_id).options(selectinload(Business.hours))
    )


def resolve_option(
    session: Session, business_id: uuid.UUID, service_slug: str, size_slug: str | None
) -> tuple[Service, SizeCategory, ServiceSizeRule]:
    """The bookable service, size, and rule, or the error explaining why not. Without a size,
    a business with only one (salons, barbershops) uses it."""
    if size_slug is None:
        sizes = session.scalars(
            select(SizeCategory).where(SizeCategory.business_id == business_id)
        ).all()
        if len(sizes) != 1:
            raise ApiError(422, "size_required", "Choose a size.")
        size = sizes[0]
    else:
        size = session.scalar(
            select(SizeCategory).where(
                SizeCategory.business_id == business_id, SizeCategory.slug == size_slug
            )
        )
    if size is None:
        raise ApiError(404, "unknown_size", "That dog size is not recognised.")
    if size.requires_staff_assessment:
        raise ApiError(
            422,
            "staff_assessment_required",
            "Dogs of this size need a staff assessment before booking. Please request a callback.",
        )
    found = session.execute(
        select(Service, ServiceSizeRule)
        .join(ServiceSizeRule, ServiceSizeRule.service_id == Service.id)
        .where(
            Service.business_id == business_id,
            Service.slug == service_slug,
            Service.is_active,
            ServiceSizeRule.size_category_id == size.id,
        )
    ).first()
    if found is None:
        raise ApiError(404, "unknown_service", "That service is not available for this dog size.")
    return found[0], size, found[1]


def check_booking_window(business: Business, day: date, now: datetime) -> None:
    today = now.astimezone(ZoneInfo(business.timezone)).date()
    if day < today:
        raise ApiError(422, "date_in_past", "That date has already passed.")
    if day > today + timedelta(days=business.booking_horizon_days):
        raise ApiError(
            422,
            "date_too_far_ahead",
            f"Appointments can be booked up to {business.booking_horizon_days} days ahead.",
        )


def day_openings(
    session: Session,
    business: Business,
    day: date,
    duration: timedelta,
    now: datetime,
    staff: list[StaffMember],
) -> DayOpenings:
    """Every start at least one of these staff can take, with who is free for it.

    For a customer with no preference, the person with the fewest bookings that day comes
    first, so work spreads across the team.
    """
    days = []
    for member in staff:
        rules = load_day_rules(session, business, day, member.id)
        days.append((member, rules, compute_slots(day, duration, rules, now)))
    days.sort(key=lambda found: (len(found[1].booked), found[0].slug))
    free: dict[datetime, tuple[Slot, list[StaffMember]]] = {}
    for member, _, slots in days:
        for slot in slots:
            free.setdefault(slot.starts_at, (slot, []))[1].append(member)
    return DayOpenings(
        closed=all(working_window(day, rules) is None for _, rules, _ in days),
        slots=[
            StaffSlot(slot.starts_at, slot.ends_at, tuple(members))
            for slot, members in sorted(free.values(), key=lambda found: found[0].starts_at)
        ],
    )


def free_staff_at(
    session: Session,
    business: Business,
    starts_at: datetime,
    duration: timedelta,
    now: datetime,
    staff: list[StaffMember],
) -> tuple[StaffMember, ...]:
    """Who could take this exact start right now, the preferred one first."""
    day = starts_at.astimezone(ZoneInfo(business.timezone)).date()
    openings = day_openings(session, business, day, duration, now, staff)
    return next((slot.staff for slot in openings.slots if slot.starts_at == starts_at), ())


def slot_is_open(
    session: Session,
    business: Business,
    starts_at: datetime,
    duration: timedelta,
    now: datetime,
    groomer_id: uuid.UUID,
    ignore_booking: uuid.UUID | None = None,
) -> bool:
    """Whether this staff member could take this exact start right now, under every rule."""
    day = starts_at.astimezone(ZoneInfo(business.timezone)).date()
    rules = load_day_rules(session, business, day, groomer_id, ignore_booking)
    return any(slot.starts_at == starts_at for slot in compute_slots(day, duration, rules, now))
