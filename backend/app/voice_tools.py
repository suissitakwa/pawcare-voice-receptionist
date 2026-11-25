"""Runs the assistant's tools for a live voice session.

Tool requests reach the backend through the browser, so every call is authenticated with
the session token, limited to the session's lifetime and a call budget, and every argument
is validated here as untrusted input. The model never receives confirmation tokens.
"""

import hmac
import json
import logging
import secrets
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.availability import (
    arrival_window,
    check_booking_window,
    day_openings,
    load_business,
    resolve_option,
    staff_for,
)
from app.booking import confirm_prepared, prepare_booking
from app.booking import sha256 as booking_sha256
from app.config import Settings
from app.errors import ApiError
from app.format_text import arrival_label, format_time
from app.manage import (
    CHANGE_LIFETIME,
    cancel,
    confirm_change,
    describe,
    find_booking,
    prepare_change,
)
from app.models import Booking, BookingDraft, CallbackRequest, VoiceSession
from app.names import clean_name
from app.prices import price_note
from app.schemas import (
    EMAIL_PATTERN,
    PHONE_PATTERN,
    PendingManage,
    PrepareBookingRequest,
    VoiceToolResult,
)
from app.voice import MAX_TOOL_CALLS, MESSAGE_TOOLS, SESSION_GRACE, sha256
from app.webhooks import callback_data, enqueue

logger = logging.getLogger("pawcare.voice")
# Managing a booking found by reference needs a spoken yes, so it is phone only.
PHONE_MANAGE_TOOLS = {
    "find_booking": find_booking,
    "prepare_change": prepare_change,
    "confirm_change": confirm_change,
    "cancel_booking": cancel,
}
# On the website the assistant only shows a change or cancellation; the visitor clicks to
# apply it.
BROWSER_MANAGE_TOOLS = {"prepare_change", "prepare_cancel"}
# A full day at 15-minute steps is under 40 starts, so the model can see every one.
MAX_TIMES_RETURNED = 40


class AvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = Field(max_length=64)
    size: str | None = Field(default=None, max_length=32)
    date: date
    preferred_time: time | None = None
    staff: str | None = Field(default=None, max_length=64)


class CallbackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=254)
    reason: str = Field(pattern="^(health|behavior|special_handling|large_dog|booking_help|other)$")
    note: str = Field(default="", max_length=300)

    _name = field_validator("name")(clean_name)


def authorize(
    session: Session,
    business_id: uuid.UUID,
    session_id: uuid.UUID,
    token: str,
    settings: Settings,
    now: datetime,
    max_seconds: int | None = None,
) -> VoiceSession:
    record = session.scalar(
        select(VoiceSession)
        .where(
            VoiceSession.id == session_id,
            VoiceSession.business_id == business_id,
            VoiceSession.token_hash == sha256(token),
        )
        .with_for_update()
    )
    if record is None:
        raise ApiError(404, "voice_session_not_found", "That voice session was not found.")
    limit = max_seconds if max_seconds is not None else settings.voice_max_session_seconds
    expires = record.started_at + timedelta(seconds=limit) + SESSION_GRACE
    if record.status != "started" or now > expires:
        raise ApiError(403, "voice_session_closed", "This voice session has ended.")
    if record.tool_calls >= MAX_TOOL_CALLS:
        raise ApiError(429, "too_many_tool_calls", "This voice session has made too many requests.")
    return record


def tool_error(code: str, message: str) -> VoiceToolResult:
    return VoiceToolResult(ok=False, result={"error": code, "message": message})


def check_availability(session: Session, business, raw: dict, now: datetime) -> VoiceToolResult:
    args = AvailabilityArgs.model_validate(raw)
    service, _, rule = resolve_option(session, business.id, args.service, args.size)
    staff = staff_for(session, business.id, service.id, args.staff)
    check_booking_window(business, args.date, now)
    timezone = ZoneInfo(business.timezone)
    window = arrival_window(business)
    openings = day_openings(
        session, business, args.date, timedelta(minutes=rule.duration_minutes), now, staff
    )
    slots = openings.slots
    result: dict = {}
    if args.preferred_time is not None:
        wanted = args.preferred_time.replace(second=0, microsecond=0)
        match = next((s for s in slots if s.starts_at.astimezone(timezone).time() == wanted), None)
        result["requested_time"] = {
            "label": format_time(wanted),
            "available": match is not None,
            "starts_at": match.starts_at.isoformat() if match else None,
        }
    return VoiceToolResult(
        ok=True,
        result={
            **result,
            "date": args.date.isoformat(),
            "closed": openings.closed,
            "duration_minutes": rule.duration_minutes,
            "price_dollars": rule.price_cents / 100,
            "times": [
                {
                    # An arrival window where the business has them: say the whole window.
                    "label": arrival_label(
                        slot.starts_at.astimezone(timezone).time(),
                        (slot.starts_at + window).astimezone(timezone).time(),
                    )
                    if window
                    else format_time(slot.starts_at.astimezone(timezone).time()),
                    "starts_at": slot.starts_at.isoformat(),
                }
                for slot in slots[:MAX_TIMES_RETURNED]
            ],
            "more_times_available": len(slots) > MAX_TIMES_RETURNED,
        },
    )


def prepare(
    session: Session, business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    raw = dict(raw)
    if raw.pop("details_read_back", None) is not True:
        return tool_error(
            "read_back_required",
            "Read the owner's name, the dog's name, and the contact details back to the "
            "caller, wait for them to say yes, then try again.",
        )
    request = PrepareBookingRequest.model_validate(raw)
    prepared = prepare_booking(session, business, request, now, voice_session_id=record.id)
    if record.channel == "phone":
        # Remembered on the server, so only the caller's yes can book exactly this.
        record.pending_draft_id = session.scalar(
            select(BookingDraft.id).where(
                BookingDraft.token_hash == booking_sha256(prepared.confirmation_token)
            )
        )
    summary = prepared.summary
    timezone = ZoneInfo(business.timezone)
    return VoiceToolResult(
        ok=True,
        result={
            "status": "awaiting_caller_confirmation",
            "service": summary.service,
            # A booking without a pet (salons, barbershops) has no dog size to mention either.
            **({"size": summary.size, "pet_name": summary.pet_name} if summary.pet_name else {}),
            "owner_name": summary.owner_name,
            **({"address": summary.service_address} if summary.service_address else {}),
            **({"job": summary.job_details} if summary.job_details else {}),
            "date": summary.local_start.date().isoformat(),
            "time": format_time(summary.local_start.astimezone(timezone).time()),
            **(
                {
                    "arrival": arrival_label(
                        summary.local_start.astimezone(timezone).time(),
                        summary.arrival_until.astimezone(timezone).time(),
                    )
                }
                if summary.arrival_until
                else {}
            ),
            "duration_minutes": summary.duration_minutes,
            "price_dollars": summary.price_cents / 100,
            **(
                {"price_note": price_note(summary.price_kind)}
                if price_note(summary.price_kind)
                else {}
            ),
            **({"with": summary.staff} if summary.staff else {}),
            "next_step": (
                "Read this summary to the caller and ask whether to book it. It is not booked yet."
                if record.channel == "phone"
                else "Ask the caller to check the summary on screen and press Confirm booking. "
                "It is not booked yet."
            ),
        },
        prepared=prepared,
    )


def create_callback(
    session: Session, business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    args = CallbackArgs.model_validate(raw)
    phone = args.phone or None
    email = (args.email or "").lower() or None
    if phone and not PHONE_PATTERN.match(phone):
        return tool_error("invalid_phone", "That phone number does not look right.")
    if email and not EMAIL_PATTERN.match(email):
        return tool_error("invalid_email", "That email address does not look right.")
    if not phone and not email:
        return tool_error("contact_required", "A phone number or email address is needed.")
    callback = CallbackRequest(
        business_id=business.id,
        voice_session_id=record.id,
        name=args.name,
        phone=phone,
        email=email,
        reason=args.reason,
        note=args.note,
    )
    session.add(callback)
    session.flush()
    enqueue(session, business.id, "callback.requested", callback_data(callback), now)
    return VoiceToolResult(
        ok=True,
        result={"status": "callback_requested", "message": "Staff will contact the caller."},
    )


def confirm_spoken(
    session: Session, business, record: VoiceSession, raw: dict, now: datetime
) -> VoiceToolResult:
    """Phone calls only. Books the summary the server prepared, never one the model describes.

    The prepared summary is remembered on the call's record, so the model can only say
    whether the caller agreed. Without a pending summary there is nothing to book.
    """
    if raw.get("caller_confirmed") is not True or set(raw) - {"caller_confirmed"}:
        return tool_error(
            "caller_confirmation_required",
            "Read the summary to the caller and ask whether to book it. Call again only if they "
            "say yes.",
        )
    draft = session.get(BookingDraft, record.pending_draft_id) if record.pending_draft_id else None
    if draft is None or draft.business_id != business.id:
        return tool_error(
            "nothing_to_confirm", "Prepare the booking first, then read its summary to the caller."
        )
    # One key per summary, so a repeated confirmation returns the same booking.
    key = "phone-" + draft.token_hash[:40]
    confirmation, _ = confirm_prepared(
        session, business, draft.token_hash, draft.summary_hash, key, now
    )
    # The caller just made it on this call, so they may change or cancel it without giving
    # the reference and contact back.
    record.managed_booking_id = session.scalar(
        select(Booking.id).where(
            Booking.business_id == business.id, Booking.reference == confirmation.reference
        )
    )
    record.pending_change = None
    return VoiceToolResult(
        ok=True,
        result={
            "status": "booked",
            "reference": confirmation.reference,
            "next_step": "Tell the caller it is booked and read the reference one character at "
            "a time. If they then want to change or cancel it, don't call find_booking: use "
            "prepare_change or cancel_booking directly.",
        },
    )


def run_tool(
    session: Session,
    business_id: uuid.UUID,
    session_id: uuid.UUID,
    name: str,
    token: str,
    arguments: str,
    settings: Settings,
    now: datetime,
    max_seconds: int | None = None,
) -> VoiceToolResult:
    """`max_seconds` is set by phone calls. Phone-only tools check the session's channel, so a
    browser session cannot book through confirm_booking."""
    record = authorize(session, business_id, session_id, token, settings, now, max_seconds)
    business = load_business(session, business_id)
    record.tool_calls += 1
    try:
        raw = json.loads(arguments) if arguments else {}
        if not isinstance(raw, dict):
            raise ValueError("arguments must be an object")
        if record.message_only and name not in MESSAGE_TOOLS:
            # A message-only call is checked here, not just by the tools it was given.
            outcome = tool_error(
                "message_only", "Only take a message on this call: call create_callback_request."
            )
        elif name == "check_availability":
            outcome = check_availability(session, business, raw, now)
        elif name == "prepare_booking":
            outcome = prepare(session, business, record, raw, now)
        elif name == "confirm_booking" and record.channel == "phone":
            outcome = confirm_spoken(session, business, record, raw, now)
        elif name in PHONE_MANAGE_TOOLS and record.channel == "phone":
            outcome = PHONE_MANAGE_TOOLS[name](session, business, record, raw, now)
        elif name in BROWSER_MANAGE_TOOLS and record.channel != "phone":
            outcome = prepare_on_screen(session, business, record, name, raw, now)
        elif name == "create_callback_request":
            outcome = create_callback(session, business, record, raw, now)
        else:
            outcome = tool_error("unknown_tool", "That tool does not exist.")
    except ValidationError as error:
        # Names of rejected fields only. The values may be personal details.
        fields = sorted({str(e["loc"][0]) for e in error.errors() if e["loc"]})
        outcome = tool_error(
            "invalid_arguments",
            "Some details were missing or not valid: " + (", ".join(fields) or "arguments") + ".",
        )
    except ValueError:
        outcome = tool_error("invalid_arguments", "The arguments must be a JSON object.")
    except ApiError as error:
        outcome = tool_error(error.code, error.message)
    if not outcome.ok:
        record.tool_failures += 1
        logger.info("Voice tool %s returned %s", name, outcome.result.get("error"))
    save_call_state(session, record)
    return outcome


def save_call_state(session: Session, record: VoiceSession) -> None:
    session.execute(
        update(VoiceSession)
        .where(VoiceSession.id == record.id)
        .values(
            tool_calls=record.tool_calls,
            tool_failures=record.tool_failures,
            managed_booking_id=record.managed_booking_id,
            pending_change=record.pending_change,
            pending_draft_id=record.pending_draft_id,
            lookup_failures=record.lookup_failures,
        )
    )


def booked_here(session: Session, record: VoiceSession) -> Booking | None:
    """The latest booking the visitor confirmed in this website conversation."""
    return session.scalar(
        select(Booking)
        .where(
            Booking.business_id == record.business_id,
            Booking.voice_session_id == record.id,
            Booking.status == "confirmed",
        )
        # The session remembers the last reference confirmed; otherwise the newest.
        .order_by(
            (Booking.reference == (record.booking_reference or "")).desc(),
            Booking.created_at.desc(),
        )
        .limit(1)
    )


def prepare_on_screen(
    session: Session, business, record: VoiceSession, name: str, raw: dict, now: datetime
) -> VoiceToolResult:
    """Website only. Shows a change or cancellation of a booking made in this conversation,
    for the visitor to confirm by clicking. The model can never apply it."""
    booking = booked_here(session, record)
    if booking is None:
        return tool_error(
            "no_booking_in_this_conversation",
            "Only a booking the caller confirmed earlier in this conversation can be changed "
            "here. For any other booking, ask them to contact the business using the details "
            "on this page.",
        )
    record.managed_booking_id = booking.id
    timezone = ZoneInfo(business.timezone)
    if name == "prepare_change":
        outcome = prepare_change(session, business, record, raw, now)
        if not outcome.ok:
            return outcome
        was, new = outcome.result["was"], outcome.result["new"]
        button = "Confirm change"
    else:
        if raw:
            return tool_error("invalid_arguments", "prepare_cancel takes no arguments.")
        if booking.starts_at <= now:
            return tool_error("booking_started", "That appointment has already started.")
        was, new = describe(booking.summary, timezone), None
        record.pending_change = {
            "action": "cancel",
            "booking_id": str(booking.id),
            "expires_at": (now + CHANGE_LIFETIME).isoformat(),
        }
        button = "Cancel booking"
    token = secrets.token_urlsafe(32)
    record.pending_change = {**record.pending_change, "token_hash": booking_sha256(token)}
    return VoiceToolResult(
        ok=True,
        result={
            "status": "awaiting_caller_confirmation",
            "reference": booking.reference,
            "was": was,
            **({"new": new} if new else {}),
            "next_step": f"Tell the caller the details are on their screen and that they must "
            f"press {button}. Nothing has changed yet.",
        },
        manage=PendingManage(
            action="change" if new else "cancel",
            reference=booking.reference,
            was=was,
            new=new,
            confirm_token=token,
        ),
    )


def confirm_on_screen(
    session: Session,
    business_id: uuid.UUID,
    session_id: uuid.UUID,
    token: str,
    confirm_token: str,
    settings: Settings,
    now: datetime,
) -> VoiceToolResult:
    """The visitor pressed Confirm change or Cancel booking. Applies exactly what was shown."""
    record = authorize(session, business_id, session_id, token, settings, now)
    if record.channel == "phone":
        raise ApiError(404, "voice_session_not_found", "That voice session was not found.")
    business = load_business(session, business_id)
    record.tool_calls += 1
    pending = record.pending_change or {}
    if not hmac.compare_digest(pending.get("token_hash", ""), booking_sha256(confirm_token)):
        raise ApiError(
            409, "nothing_to_confirm", "That change is no longer waiting. Ask the assistant again."
        )
    if pending.get("action") == "cancel":
        if datetime.fromisoformat(pending["expires_at"]) <= now:
            record.pending_change = None
            outcome = tool_error("change_expired", "That expired. Ask the assistant again.")
        else:
            outcome = cancel(session, business, record, {"caller_confirmed": True}, now)
    else:
        outcome = confirm_change(session, business, record, {"caller_confirmed": True}, now)
    save_call_state(session, record)
    return outcome
