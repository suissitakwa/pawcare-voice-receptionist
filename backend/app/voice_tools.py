"""Runs the assistant's tools for a live voice session.

Tool requests reach the backend through the browser, so every call is authenticated with
the session token, limited to the session's lifetime and a call budget, and every argument
is validated here as untrusted input. The model never receives confirmation tokens.
"""

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.availability import (
    check_booking_window,
    compute_slots,
    load_business,
    load_day_rules,
    resolve_option,
    working_window,
)
from app.booking import prepare_booking
from app.config import Settings
from app.errors import ApiError
from app.format_text import format_time
from app.models import CallbackRequest, VoiceSession
from app.schemas import EMAIL_PATTERN, PHONE_PATTERN, PrepareBookingRequest, VoiceToolResult
from app.voice import MAX_TOOL_CALLS, SESSION_GRACE, sha256

logger = logging.getLogger("pawcare.voice")
MAX_TIMES_RETURNED = 12


class AvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = Field(max_length=64)
    size: str = Field(max_length=32)
    date: date


class CallbackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=254)
    reason: str = Field(pattern="^(health|behavior|special_handling|large_dog|booking_help|other)$")
    note: str = Field(default="", max_length=300)


def authorize(
    session: Session,
    business_id: uuid.UUID,
    session_id: uuid.UUID,
    token: str,
    settings: Settings,
    now: datetime,
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
    expires = (
        record.started_at + timedelta(seconds=settings.voice_max_session_seconds) + SESSION_GRACE
    )
    if record.status != "started" or now > expires:
        raise ApiError(403, "voice_session_closed", "This voice session has ended.")
    if record.tool_calls >= MAX_TOOL_CALLS:
        raise ApiError(429, "too_many_tool_calls", "This voice session has made too many requests.")
    return record


def tool_error(code: str, message: str) -> VoiceToolResult:
    return VoiceToolResult(ok=False, result={"error": code, "message": message})


def check_availability(session: Session, business, raw: dict, now: datetime) -> VoiceToolResult:
    args = AvailabilityArgs.model_validate(raw)
    _, _, rule = resolve_option(session, business.id, args.service, args.size)
    check_booking_window(business, args.date, now)
    rules = load_day_rules(session, business, args.date)
    timezone = ZoneInfo(business.timezone)
    slots = compute_slots(args.date, timedelta(minutes=rule.duration_minutes), rules, now)
    return VoiceToolResult(
        ok=True,
        result={
            "date": args.date.isoformat(),
            "closed": working_window(args.date, rules) is None,
            "duration_minutes": rule.duration_minutes,
            "price_dollars": rule.price_cents / 100,
            "times": [
                {
                    "label": format_time(slot.starts_at.astimezone(timezone).time()),
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
    request = PrepareBookingRequest.model_validate(raw)
    prepared = prepare_booking(session, business, request, now, voice_session_id=record.id)
    summary = prepared.summary
    timezone = ZoneInfo(business.timezone)
    return VoiceToolResult(
        ok=True,
        result={
            "status": "awaiting_caller_confirmation",
            "service": summary.service,
            "size": summary.size,
            "pet_name": summary.pet_name,
            "owner_name": summary.owner_name,
            "date": summary.local_start.date().isoformat(),
            "time": format_time(summary.local_start.astimezone(timezone).time()),
            "duration_minutes": summary.duration_minutes,
            "price_dollars": summary.price_cents / 100,
            "next_step": "Ask the caller to check the summary on screen and press Confirm "
            "booking. It is not booked yet.",
        },
        prepared=prepared,
    )


def create_callback(session: Session, business, record: VoiceSession, raw: dict) -> VoiceToolResult:
    args = CallbackArgs.model_validate(raw)
    phone = args.phone or None
    email = (args.email or "").lower() or None
    if phone and not PHONE_PATTERN.match(phone):
        return tool_error("invalid_phone", "That phone number does not look right.")
    if email and not EMAIL_PATTERN.match(email):
        return tool_error("invalid_email", "That email address does not look right.")
    if not phone and not email:
        return tool_error("contact_required", "A phone number or email address is needed.")
    session.add(
        CallbackRequest(
            business_id=business.id,
            voice_session_id=record.id,
            name=args.name,
            phone=phone,
            email=email,
            reason=args.reason,
            note=args.note,
        )
    )
    return VoiceToolResult(
        ok=True,
        result={"status": "callback_requested", "message": "Staff will contact the caller."},
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
) -> VoiceToolResult:
    record = authorize(session, business_id, session_id, token, settings, now)
    business = load_business(session, business_id)
    record.tool_calls += 1
    try:
        raw = json.loads(arguments) if arguments else {}
        if not isinstance(raw, dict):
            raise ValueError("arguments must be an object")
        if name == "check_availability":
            outcome = check_availability(session, business, raw, now)
        elif name == "prepare_booking":
            outcome = prepare(session, business, record, raw, now)
        elif name == "create_callback_request":
            outcome = create_callback(session, business, record, raw)
        else:
            outcome = tool_error("unknown_tool", "That tool does not exist.")
    except (ValueError, ValidationError):
        # Names of rejected fields only. The values may be personal details.
        outcome = tool_error("invalid_arguments", "Some details were missing or not valid.")
    except ApiError as error:
        outcome = tool_error(error.code, error.message)
    if not outcome.ok:
        record.tool_failures += 1
        logger.info("Voice tool %s returned %s", name, outcome.result.get("error"))
    session.execute(
        update(VoiceSession)
        .where(VoiceSession.id == record.id)
        .values(tool_calls=record.tool_calls, tool_failures=record.tool_failures)
    )
    return outcome
