"""Phone calls, answered by OpenAI Realtime through its SIP connection.

A phone number (for example from Twilio) forwards calls to OpenAI. OpenAI sends a signed
`realtime.call.incoming` webhook, and the backend decides whether to answer: phone calls must
be switched on, and the daily and per-caller limits must allow it. Accepting sends the
instructions, voice and tools. The backend then joins the call over a server-side WebSocket and
runs every tool itself, so no browser is involved and the confirmation token never leaves the
server. The caller confirms a booking out loud, and the backend books only the summary it
prepared.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Business, VoiceSession
from app.schemas import PreparedBooking, VoiceToolResult, VoiceUsage
from app.voice import (
    TRANSCRIPTION_MODEL,
    build_instructions,
    local_day_start,
    phone_tools,
    sha256,
)
from app.voice_tools import run_tool, tool_error

logger = logging.getLogger("pawcare.phone")

CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_URL = "wss://api.openai.com/v1/realtime"
# OpenAI signs webhooks the Standard Webhooks way. Older deliveries are refused as replays.
SIGNATURE_TOLERANCE_SECONDS = 300
CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
CALLER_NUMBER_PATTERN = re.compile(r"sip:(\+?\d{7,15})@")
# SIP status codes sent to the carrier when a call is not answered.
DECLINE = 603
BUSY = 486


class InvalidSignature(Exception):
    pass


class CallControlError(Exception):
    pass


def verify_signature(secret: str, headers: Mapping[str, str], body: bytes, now: datetime) -> None:
    """Raises InvalidSignature unless the body was signed by OpenAI with this secret."""
    message_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signatures = headers.get("webhook-signature", "")
    if not (message_id and timestamp and signatures):
        raise InvalidSignature("missing signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise InvalidSignature("bad timestamp") from None
    if abs(now.timestamp() - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise InvalidSignature("timestamp outside tolerance")
    try:
        key = (
            base64.b64decode(secret.removeprefix("whsec_"), validate=True)
            if secret.startswith("whsec_")
            else secret.encode()
        )
    except ValueError:
        raise InvalidSignature("unusable secret") from None
    signed = f"{message_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signatures.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise InvalidSignature("no matching signature")


def caller_number(sip_headers: list) -> str | None:
    for header in sip_headers:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "from":
            match = CALLER_NUMBER_PATTERN.search(str(header.get("value", "")))
            return match.group(1) if match else None
    return None


def call_token(call_id: str) -> str:
    # The call id identifies the session, as a session token does for the browser.
    return f"call:{call_id}"


def accept_body(settings: Settings, instructions: str) -> dict:
    return {
        "type": "realtime",
        "model": settings.voice_model,
        "instructions": instructions,
        "tools": phone_tools(),
        "tool_choice": "auto",
        "audio": {
            "input": {
                "transcription": {"model": TRANSCRIPTION_MODEL, "language": "en"},
                # A handset is held close to the mouth.
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "auto",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": settings.voice_name},
        },
    }


class OpenAICalls:
    """Accept, reject and hang up through OpenAI's call endpoints."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def post(self, call_id: str, action: str, body: dict | None = None) -> None:
        if not CALL_ID_PATTERN.match(call_id):
            raise CallControlError("invalid call id")
        # A fixed https URL; the call id was checked above.
        request = urllib.request.Request(  # noqa: S310
            f"{CALLS_URL}/{call_id}/{action}",
            data=json.dumps(body or {}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10):  # noqa: S310
                return
        except urllib.error.HTTPError as error:
            # The status only. The body could echo the instructions.
            raise CallControlError(f"{action} returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CallControlError(f"{action} failed: {type(error).__name__}") from None

    def accept(self, call_id: str, body: dict) -> None:
        self.post(call_id, "accept", body)

    def reject(self, call_id: str, status_code: int) -> None:
        self.post(call_id, "reject", {"status_code": status_code})

    def hangup(self, call_id: str) -> None:
        self.post(call_id, "hangup")


def answer_problem(settings: Settings) -> str | None:
    if not settings.phone_enabled:
        return "phone calls are off"
    if settings.voice_mode != "live" or not settings.openai_api_key:
        return "voice is not live"
    return None


def handle_incoming(
    session: Session,
    business: Business,
    settings: Settings,
    data: dict,
    calls: OpenAICalls,
    now: datetime,
) -> VoiceSession | None:
    """Answers or declines one incoming call. Returns the session to run, or None."""
    call_id = data.get("call_id")
    if not isinstance(call_id, str) or not CALL_ID_PATTERN.match(call_id):
        logger.warning("Incoming call event without a usable call id")
        return None
    token_hash = sha256(call_token(call_id))
    if session.scalar(select(VoiceSession.id).where(VoiceSession.token_hash == token_hash)):
        # OpenAI retried the webhook. The call was handled the first time.
        return None

    def decline(status_code: int, why: str) -> None:
        logger.info("Declining a call: %s", why)
        try:
            calls.reject(call_id, status_code)
        except CallControlError as error:
            logger.warning("Rejecting a call failed: %s", error)

    problem = answer_problem(settings)
    if problem is not None:
        decline(DECLINE, problem)
        return None

    timezone = ZoneInfo(business.timezone)
    today = now.astimezone(timezone).date()
    calls_today = session.scalar(
        select(func.count())
        .select_from(VoiceSession)
        .where(
            VoiceSession.business_id == business.id,
            VoiceSession.channel == "phone",
            VoiceSession.started_at >= local_day_start(today, timezone),
        )
    )
    if calls_today >= settings.phone_daily_call_limit:
        decline(BUSY, "daily call limit reached")
        return None
    sip_headers = data.get("sip_headers")
    number = caller_number(sip_headers if isinstance(sip_headers, list) else [])
    # Only a hash of the number is kept, to limit repeat calls from one caller.
    caller_hash = sha256(f"{business.id}:phone:{number or 'withheld'}")
    recent = session.scalar(
        select(func.count())
        .select_from(VoiceSession)
        .where(
            VoiceSession.client_hash == caller_hash,
            VoiceSession.started_at >= now - timedelta(hours=1),
        )
    )
    if recent >= settings.phone_calls_per_caller_per_hour:
        decline(BUSY, "too many calls from one caller")
        return None

    record = VoiceSession(
        id=uuid.uuid4(),
        business_id=business.id,
        mode="live",
        channel="phone",
        model=settings.voice_model,
        client_hash=caller_hash,
        token_hash=token_hash,
        started_at=now,
    )
    session.add(record)
    session.flush()
    instructions = build_instructions(session, business, today, "phone", number)
    try:
        calls.accept(call_id, accept_body(settings, instructions))
    except CallControlError as error:
        logger.error("Accepting a call failed: %s", error)
        record.status = "ended"
        record.ended_at = now
        record.end_reason = "accept_failed"
        return None
    return record


class CallState:
    """What one call keeps in memory while it runs."""

    def __init__(self) -> None:
        self.pending: PreparedBooking | None = None
        self.usage = VoiceUsage(input_tokens=0, output_tokens=0, responses=0)

    def count_response(self, response: dict) -> None:
        usage = response.get("usage") or {}
        reason = (response.get("status_details") or {}).get("reason")
        self.usage = VoiceUsage(
            input_tokens=self.usage.input_tokens + int(usage.get("input_tokens") or 0),
            output_tokens=self.usage.output_tokens + int(usage.get("output_tokens") or 0),
            responses=self.usage.responses + 1,
            incomplete_responses=self.usage.incomplete_responses
            + (response.get("status") == "incomplete"),
            interrupted_responses=self.usage.interrupted_responses
            + (response.get("status") == "cancelled" and reason == "turn_detected"),
        )


def connect_to_call(call_id: str, api_key: str):
    from websockets.asyncio.client import connect

    return connect(
        f"{REALTIME_URL}?call_id={call_id}",
        additional_headers={"Authorization": f"Bearer {api_key}"},
        max_size=4 * 1024 * 1024,
    )


async def run_call(
    call_id: str,
    session_id: uuid.UUID,
    business_id: uuid.UUID,
    settings: Settings,
    get_engine,
    clock: Callable[[], datetime],
    calls: OpenAICalls,
    connect=connect_to_call,
) -> str:
    """Runs the tools for one call until it ends. Returns why it ended."""
    state = CallState()
    reason = "caller_hung_up"

    def run_one(name: str, arguments: str) -> VoiceToolResult:
        with Session(get_engine()) as session:
            try:
                outcome = run_tool(
                    session,
                    business_id,
                    session_id,
                    name,
                    call_token(call_id),
                    arguments,
                    settings,
                    clock(),
                    max_seconds=settings.phone_max_call_seconds,
                    pending=state.pending,
                )
                session.commit()
                return outcome
            except Exception:
                session.rollback()
                logger.exception("Phone tool %s failed", name)
                return tool_error("tool_unavailable", "That did not work. Offer a callback.")

    timer = asyncio.timeout(settings.phone_max_call_seconds)
    try:
        async with timer:
            async with connect(call_id, settings.openai_api_key) as socket:
                # The model speaks first, as a receptionist does.
                await socket.send(json.dumps({"type": "response.create"}))
                async for message in socket:
                    event = json.loads(message)
                    kind = event.get("type")
                    if kind == "error":
                        logger.warning(
                            "Phone call error event %s", (event.get("error") or {}).get("code")
                        )
                    if kind != "response.done":
                        continue
                    response = event.get("response") or {}
                    state.count_response(response)
                    tool_calls = [
                        item
                        for item in response.get("output") or []
                        if item.get("type") == "function_call"
                        and isinstance(item.get("name"), str)
                        and isinstance(item.get("call_id"), str)
                    ]
                    for item in tool_calls:
                        outcome = await asyncio.to_thread(
                            run_one, item["name"], item.get("arguments") or ""
                        )
                        if outcome.prepared is not None:
                            state.pending = outcome.prepared
                        # Only the result reaches the model, never the confirmation token.
                        output = {"ok": outcome.ok, **outcome.result}
                        await socket.send(
                            json.dumps(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": item["call_id"],
                                        "output": json.dumps(output),
                                    },
                                }
                            )
                        )
                    if tool_calls:
                        await socket.send(json.dumps({"type": "response.create"}))
    except TimeoutError:
        if timer.expired():
            reason = "time_limit"
            try:
                await asyncio.to_thread(calls.hangup, call_id)
            except CallControlError as error:
                logger.warning("Hanging up failed: %s", error)
        else:
            logger.warning("Joining the phone call timed out")
            reason = "connection_error"
    except asyncio.CancelledError:
        reason = "server_stopped"
        await asyncio.to_thread(finish_call, get_engine, session_id, reason, state, clock())
        raise
    except Exception as error:
        # The details stay out of the log; they could include call content.
        logger.warning("Phone call connection ended with %s", type(error).__name__)
        reason = "connection_error"
    await asyncio.to_thread(finish_call, get_engine, session_id, reason, state, clock())
    return reason


def finish_call(get_engine, session_id: uuid.UUID, reason: str, state: CallState, now) -> None:
    with Session(get_engine()) as session:
        record = session.get(VoiceSession, session_id)
        if record is None or record.status == "ended":
            return
        record.status = "ended"
        record.ended_at = now
        record.end_reason = reason
        record.usage = state.usage.model_dump()
        session.commit()
