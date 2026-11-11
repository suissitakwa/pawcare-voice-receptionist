"""Two-step booking: prepare a draft the customer reviews, then confirm exactly that draft.

PostgreSQL enforces the guarantees. The `bookings_no_overlap` exclusion constraint rejects
overlapping confirmed bookings even when requests race, and the unique idempotency key turns
a retried confirmation into a replay of the original result. The checks in this module come
first only so that callers get a clear error rather than a constraint violation.
"""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.availability import (
    active_groomer_id,
    check_booking_window,
    resolve_option,
    slot_is_open,
)
from app.errors import ApiError
from app.models import Booking, BookingDraft, Business, VoiceSession
from app.schemas import BookingConfirmation, BookingSummary, PrepareBookingRequest, PreparedBooking
from app.webhooks import booking_data, enqueue

DRAFT_LIFETIME = timedelta(minutes=10)
# No 0, O, 1, I, or L, so a reference can be read aloud and typed back without confusion.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def summary_hash(summary: dict) -> str:
    return sha256(json.dumps(summary, sort_keys=True, separators=(",", ":")))


def new_reference() -> str:
    return "PC-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(8))


def slot_unavailable() -> ApiError:
    return ApiError(
        409, "slot_unavailable", "That time is no longer available. Please choose another."
    )


def prepare_booking(
    session: Session,
    business: Business,
    request: PrepareBookingRequest,
    now: datetime,
    voice_session_id: uuid.UUID | None = None,
) -> PreparedBooking:
    service, size, rule = resolve_option(session, business.id, request.service, request.size)
    timezone = ZoneInfo(business.timezone)
    starts_at = request.starts_at.astimezone(UTC)
    check_booking_window(business, starts_at.astimezone(timezone).date(), now)
    duration = timedelta(minutes=rule.duration_minutes)
    groomer_id = active_groomer_id(session, business.id)
    if groomer_id is None or not slot_is_open(session, business, starts_at, duration, now):
        raise slot_unavailable()

    ends_at = starts_at + duration
    summary = BookingSummary(
        service=service.name,
        size=size.label,
        pet_name=request.pet_name,
        owner_name=request.owner_name,
        owner_email=request.owner_email,
        owner_phone=request.owner_phone,
        starts_at=starts_at,
        local_start=starts_at.astimezone(timezone),
        local_end=ends_at.astimezone(timezone),
        timezone=business.timezone,
        duration_minutes=rule.duration_minutes,
        price_cents=rule.price_cents,
    ).model_dump(mode="json")
    token = secrets.token_urlsafe(32)
    draft = BookingDraft(
        business_id=business.id,
        groomer_id=groomer_id,
        service_id=service.id,
        size_category_id=size.id,
        starts_at=starts_at,
        ends_at=ends_at,
        occupied_until=ends_at + timedelta(minutes=business.buffer_minutes),
        price_cents=rule.price_cents,
        owner_name=request.owner_name,
        owner_email=request.owner_email,
        owner_phone=request.owner_phone,
        pet_name=request.pet_name,
        summary=summary,
        summary_hash=summary_hash(summary),
        # Only a hash is stored, so the database alone cannot be used to confirm a draft.
        token_hash=sha256(token),
        expires_at=now + DRAFT_LIFETIME,
        voice_session_id=voice_session_id,
    )
    session.add(draft)
    session.flush()
    return PreparedBooking(
        confirmation_token=token,
        summary_hash=draft.summary_hash,
        expires_at=draft.expires_at,
        summary=BookingSummary.model_validate(summary),
    )


def to_confirmation(booking: Booking) -> BookingConfirmation:
    return BookingConfirmation(
        reference=booking.reference,
        status=booking.status,
        summary=BookingSummary.model_validate(booking.summary),
    )


def replay(booking: Booking, request_hash: str) -> tuple[BookingConfirmation, bool]:
    if booking.request_hash != request_hash:
        raise ApiError(
            422,
            "idempotency_key_reused",
            "This idempotency key was already used for a different booking request.",
        )
    return to_confirmation(booking), False


def confirm_booking(
    session: Session,
    business: Business,
    token: str,
    expected_summary_hash: str,
    idempotency_key: str,
    now: datetime,
) -> tuple[BookingConfirmation, bool]:
    """Returns the confirmation and whether this call created the booking."""
    return confirm_prepared(
        session, business, sha256(token), expected_summary_hash, idempotency_key, now
    )


def confirm_prepared(
    session: Session,
    business: Business,
    request_hash: str,
    expected_summary_hash: str,
    idempotency_key: str,
    now: datetime,
) -> tuple[BookingConfirmation, bool]:
    """Confirms by the token's hash. Only the server holds that pairing for phone calls."""

    def booking_for_key() -> Booking | None:
        return session.scalar(
            select(Booking).where(
                Booking.business_id == business.id, Booking.idempotency_key == idempotency_key
            )
        )

    existing = booking_for_key()
    if existing is not None:
        return replay(existing, request_hash)

    # The row lock makes concurrent confirmations of one draft take turns.
    draft = session.scalar(
        select(BookingDraft)
        .where(BookingDraft.business_id == business.id, BookingDraft.token_hash == request_hash)
        .with_for_update()
    )
    if draft is None:
        raise ApiError(404, "confirmation_not_found", "That booking summary was not found.")
    already = session.scalar(select(Booking).where(Booking.draft_id == draft.id))
    if already is not None:
        if already.idempotency_key == idempotency_key:
            return replay(already, request_hash)
        raise ApiError(409, "already_confirmed", "This booking summary was already confirmed.")
    if draft.expires_at <= now:
        raise ApiError(
            410, "confirmation_expired", "This booking summary expired. Please prepare it again."
        )
    if draft.summary_hash != expected_summary_hash:
        raise ApiError(
            409,
            "confirmation_mismatch",
            "The details changed since they were shown. Please review and confirm again.",
        )
    duration = draft.ends_at - draft.starts_at
    if not slot_is_open(session, business, draft.starts_at, duration, now):
        raise slot_unavailable()

    for _ in range(3):
        booking = Booking(
            business_id=business.id,
            groomer_id=draft.groomer_id,
            service_id=draft.service_id,
            size_category_id=draft.size_category_id,
            starts_at=draft.starts_at,
            ends_at=draft.ends_at,
            occupied_until=draft.occupied_until,
            status="confirmed",
            reference=new_reference(),
            price_cents=draft.price_cents,
            owner_name=draft.owner_name,
            owner_email=draft.owner_email,
            owner_phone=draft.owner_phone,
            pet_name=draft.pet_name,
            summary=draft.summary,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            draft_id=draft.id,
            voice_session_id=draft.voice_session_id,
            # The same clock as every other booking rule, so metrics windows line up.
            created_at=now,
        )
        try:
            with session.begin_nested():
                session.add(booking)
                session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "bookings_no_overlap":
                raise slot_unavailable() from None
            if constraint == "uq_bookings_idempotency_key":
                return replay(booking_for_key(), request_hash)
            if constraint == "uq_bookings_draft_id":
                raise ApiError(
                    409, "already_confirmed", "This booking summary was already confirmed."
                ) from None
            if constraint == "uq_bookings_reference":
                continue
            raise
        if draft.voice_session_id is not None:
            session.execute(
                update(VoiceSession)
                .where(VoiceSession.id == draft.voice_session_id)
                .values(booking_reference=booking.reference)
            )
        enqueue(session, business.id, "booking.confirmed", booking_data(booking, business), now)
        return to_confirmation(booking), True
    raise ApiError(503, "reference_unavailable", "Please try confirming again.")
