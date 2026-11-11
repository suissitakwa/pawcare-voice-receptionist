"""Changing or cancelling an existing booking during a phone call.

A caller first proves they own a booking: its reference plus the phone number or email on
it. The server remembers which booking that was, so later tools never take a reference from
the model. Changes and cancellations work like new bookings: the server prepares exactly what
will happen, the assistant reads it out, and only the caller's spoken yes applies it.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.availability import check_booking_window, resolve_option, slot_is_open
from app.booking import slot_unavailable, summary_hash
from app.format_text import format_time
from app.models import Booking, Business, Service, SizeCategory, VoiceSession
from app.schemas import BookingSummary, VoiceToolResult
from app.webhooks import booking_data, enqueue

MAX_LOOKUP_FAILURES = 3
CHANGE_LIFETIME = timedelta(minutes=10)


class FindArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reference: str = Field(min_length=4, max_length=32)
    contact: str = Field(min_length=3, max_length=254)


class ChangeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    service: str | None = Field(default=None, max_length=64)
    size: str | None = Field(default=None, max_length=32)


def error(code: str, message: str) -> VoiceToolResult:
    return VoiceToolResult(ok=False, result={"error": code, "message": message})


def normalize_reference(spoken: str) -> str:
    """'pc 7 7 p f q n w 8' and 'PC-77PFQNW8' both become 'PC-77PFQNW8'."""
    letters = re.sub(r"[^A-Z0-9]", "", spoken.upper())
    return "PC-" + letters.removeprefix("PC")


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def contact_matches(booking: Booking, contact: str) -> bool:
    if "@" in contact:
        return bool(booking.owner_email) and booking.owner_email.lower() == contact.lower()
    spoken = digits(contact)
    stored = digits(booking.owner_phone or "")
    # The last ten digits, so "+1 260…" and "260…" are the same number.
    return len(spoken) >= 7 and bool(stored) and spoken[-10:] == stored[-10:]


def describe(booking_summary: dict, timezone: ZoneInfo) -> dict:
    start = datetime.fromisoformat(booking_summary["local_start"]).astimezone(timezone)
    return {
        "service": booking_summary["service"],
        "size": booking_summary["size"],
        "pet_name": booking_summary["pet_name"],
        "date": start.date().isoformat(),
        "weekday": f"{start:%A}",
        "time": format_time(start.time()),
        "duration_minutes": booking_summary["duration_minutes"],
        "price_dollars": booking_summary["price_cents"] / 100,
    }


def managed_booking(session: Session, record: VoiceSession, lock: bool = False) -> Booking | None:
    if record.managed_booking_id is None:
        return None
    query = select(Booking).where(
        Booking.id == record.managed_booking_id, Booking.business_id == record.business_id
    )
    return session.scalar(query.with_for_update() if lock else query)


def not_found() -> VoiceToolResult:
    # The same answer for an unknown reference and a wrong contact, so references can't be
    # confirmed by guessing.
    return error(
        "booking_not_found",
        "No booking matches that reference and contact. Read back what you heard and ask the "
        "caller to check it.",
    )


def find_booking(
    session: Session, business: Business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    if record.lookup_failures >= MAX_LOOKUP_FAILURES:
        return error(
            "too_many_attempts",
            "Too many lookups on this call. Offer a callback so staff can help instead.",
        )
    args = FindArgs.model_validate(raw)
    booking = session.scalar(
        select(Booking).where(
            Booking.business_id == business.id,
            Booking.reference == normalize_reference(args.reference),
        )
    )
    if booking is None or not contact_matches(booking, args.contact):
        record.lookup_failures += 1
        return not_found()
    if booking.status != "confirmed":
        return error("already_cancelled", "That booking was already cancelled.")
    if booking.starts_at <= now:
        return error(
            "booking_started",
            "That appointment has already started or passed, so it can't be changed by phone.",
        )
    record.managed_booking_id = booking.id
    record.pending_change = None
    return VoiceToolResult(
        ok=True,
        result={
            "status": "found",
            "reference": booking.reference,
            "owner_name": booking.owner_name,
            **describe(booking.summary, ZoneInfo(business.timezone)),
            "next_step": "Tell the caller what you found and ask whether they want to change "
            "the time or service, or cancel it.",
        },
    )


def find_first() -> VoiceToolResult:
    return error(
        "booking_not_identified",
        "Ask for the booking reference and the phone number or email on the booking, then "
        "call find_booking.",
    )


def option_slugs(session: Session, booking: Booking) -> tuple[str, str]:
    service = session.scalar(select(Service.slug).where(Service.id == booking.service_id))
    size = session.scalar(
        select(SizeCategory.slug).where(SizeCategory.id == booking.size_category_id)
    )
    return service, size


def prepare_change(
    session: Session, business: Business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    booking = managed_booking(session, record)
    if booking is None or booking.status != "confirmed":
        return find_first()
    args = ChangeArgs.model_validate(raw)
    current_service, current_size = option_slugs(session, booking)
    service, size, rule = resolve_option(
        session, business.id, args.service or current_service, args.size or current_size
    )
    timezone = ZoneInfo(business.timezone)
    starts_at = args.starts_at.astimezone(ZoneInfo("UTC"))
    check_booking_window(business, starts_at.astimezone(timezone).date(), now)
    duration = timedelta(minutes=rule.duration_minutes)
    if not slot_is_open(session, business, starts_at, duration, now, ignore_booking=booking.id):
        raise slot_unavailable()
    ends_at = starts_at + duration
    summary = BookingSummary(
        service=service.name,
        size=size.label,
        pet_name=booking.pet_name,
        owner_name=booking.owner_name,
        owner_email=booking.owner_email,
        owner_phone=booking.owner_phone,
        starts_at=starts_at,
        local_start=starts_at.astimezone(timezone),
        local_end=ends_at.astimezone(timezone),
        timezone=business.timezone,
        duration_minutes=rule.duration_minutes,
        price_cents=rule.price_cents,
    ).model_dump(mode="json")
    record.pending_change = {
        "booking_id": str(booking.id),
        "service_id": str(service.id),
        "size_category_id": str(size.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "occupied_until": (ends_at + timedelta(minutes=business.buffer_minutes)).isoformat(),
        "price_cents": rule.price_cents,
        "summary": summary,
        "summary_hash": summary_hash(summary),
        "expires_at": (now + CHANGE_LIFETIME).isoformat(),
    }
    return VoiceToolResult(
        ok=True,
        result={
            "status": "awaiting_caller_confirmation",
            "was": describe(booking.summary, timezone),
            "new": describe(summary, timezone),
            "next_step": "Read the new details to the caller and ask whether to make the "
            "change. Nothing has changed yet.",
        },
    )


def confirmation_required(action: str) -> VoiceToolResult:
    return error(
        "caller_confirmation_required",
        f"Read the details to the caller and ask whether to {action}. Call again only if they "
        "say yes.",
    )


def confirm_change(
    session: Session, business: Business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    if raw != {"caller_confirmed": True}:
        return confirmation_required("make the change")
    pending = record.pending_change
    booking = managed_booking(session, record, lock=True)
    if booking is None:
        return find_first()
    if pending is None or pending["booking_id"] != str(booking.id):
        return error("nothing_to_confirm", "Prepare the change first, then read it out.")
    if datetime.fromisoformat(pending["expires_at"]) <= now:
        record.pending_change = None
        return error("change_expired", "That change expired. Check the time again.")
    if booking.status != "confirmed" or booking.starts_at <= now:
        record.pending_change = None
        return error("booking_unavailable", "That booking can no longer be changed.")
    starts_at = datetime.fromisoformat(pending["starts_at"])
    ends_at = datetime.fromisoformat(pending["ends_at"])
    if not slot_is_open(
        session, business, starts_at, ends_at - starts_at, now, ignore_booking=booking.id
    ):
        record.pending_change = None
        raise slot_unavailable()
    try:
        with session.begin_nested():
            booking.service_id = pending["service_id"]
            booking.size_category_id = pending["size_category_id"]
            booking.starts_at = starts_at
            booking.ends_at = ends_at
            booking.occupied_until = datetime.fromisoformat(pending["occupied_until"])
            booking.price_cents = pending["price_cents"]
            booking.summary = pending["summary"]
            booking.changed_at = now
            session.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        session.refresh(booking)
        record.pending_change = None
        if constraint == "bookings_no_overlap":
            raise slot_unavailable() from None
        raise
    record.pending_change = None
    enqueue(session, business.id, "booking.changed", booking_data(booking, business), now)
    return VoiceToolResult(
        ok=True,
        result={
            "status": "changed",
            "reference": booking.reference,
            **describe(booking.summary, ZoneInfo(business.timezone)),
            "next_step": "Tell the caller the change is made. The reference stays the same.",
        },
    )


def cancel(
    session: Session, business: Business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    if raw != {"caller_confirmed": True}:
        return confirmation_required("cancel the booking")
    booking = managed_booking(session, record, lock=True)
    if booking is None:
        return find_first()
    if booking.status == "cancelled":
        # A repeated yes on the same call gets the same answer.
        return VoiceToolResult(
            ok=True, result={"status": "cancelled", "reference": booking.reference}
        )
    if booking.starts_at <= now:
        return error("booking_started", "That appointment has already started.")
    booking.status = "cancelled"
    booking.cancelled_at = now
    booking.cancelled_via = "caller"
    record.pending_change = None
    session.flush()
    enqueue(session, business.id, "booking.cancelled", booking_data(booking, business), now)
    return VoiceToolResult(
        ok=True,
        result={
            "status": "cancelled",
            "reference": booking.reference,
            "next_step": "Tell the caller it is cancelled, and offer to book another time.",
        },
    )
