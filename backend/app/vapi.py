"""Phone calls through Vapi.

Vapi handles the phone line, speech recognition, the voice and turn-taking. This backend
stays in charge of everything else, through one endpoint Vapi calls:

- `assistant-request` at the start of each call: the backend applies the phone limits and
  answers with the whole assistant (instructions from the database, phone tools, voice).
- `tool-calls` during the call: the tools run here, with the same validation, limits and
  spoken-confirmation rules as OpenAI phone calls.
- `end-of-call-report` and `status-update` at the end: the call is recorded as ended, with
  Vapi's reported cost.

Every request must carry the shared secret. Calls are voice sessions with channel `phone`
and provider `vapi`.
"""

import hmac
import json
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Business, VoiceSession
from app.phone import CALL_ID_PATTERN, hash_caller, limit_problem
from app.voice import build_instructions, phone_tools, sha256
from app.voice_tools import run_tool

logger = logging.getLogger("pawcare.vapi")

TRANSCRIPTION_LANGUAGE = "en"
SERVER_MESSAGES = ["tool-calls", "end-of-call-report", "status-update"]
# Vapi's reasons, shortened to the end reasons used for every voice session.
END_REASONS = {
    "customer-ended-call": "caller_hung_up",
    "assistant-ended-call": "assistant_ended",
    "exceeded-max-duration": "time_limit",
    "silence-timed-out": "silence",
}
DECLINED_MESSAGE = (
    "Sorry, our assistant can't take calls right now. Please try again later or book on our "
    "website."
)


def secret_matches(settings: Settings, headers) -> bool:
    """Vapi sends the secret as a bearer token or, in the older style, as X-Vapi-Secret."""
    if not settings.vapi_secret:
        return False
    sent = headers.get("x-vapi-secret") or ""
    authorization = headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        sent = sent or authorization[7:]
    return hmac.compare_digest(sent.encode(), settings.vapi_secret.encode())


def call_token(call_id: str) -> str:
    return f"vapi:{call_id}"


def vapi_tools() -> list[dict]:
    """The phone tools in Vapi's shape. With no server of their own, calls come to the
    assistant's server, this backend."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in phone_tools()
    ]


def first_message(business: Business) -> str:
    return f"Hi, thanks for calling {business.name}! I'm the AI assistant. How can I help?"


def assistant(settings: Settings, business: Business, instructions: str, server_url: str) -> dict:
    config: dict = {
        "name": f"{business.name} receptionist"[:40],
        "firstMessage": first_message(business),
        "model": {
            "provider": "openai",
            "model": settings.vapi_model,
            "temperature": 0.4,
            "messages": [{"role": "system", "content": instructions}],
            "tools": vapi_tools(),
        },
        "voice": {"provider": settings.vapi_voice_provider, "voiceId": settings.vapi_voice_id},
        "transcriber": {
            "provider": "deepgram",
            "model": settings.vapi_transcriber_model,
            "language": TRANSCRIPTION_LANGUAGE,
        },
        "maxDurationSeconds": settings.phone_max_call_seconds,
        "serverMessages": SERVER_MESSAGES,
        # Tool calls and the end-of-call report come back here, with the secret, because a
        # per-call assistant gets no stored credential.
        "server": {
            "url": server_url,
            "headers": {"Authorization": f"Bearer {settings.vapi_secret}"},
        },
    }
    if settings.vapi_assistant_overrides:
        try:
            overrides = json.loads(settings.vapi_assistant_overrides)
        except ValueError:
            logger.error("VAPI_ASSISTANT_OVERRIDES is not valid JSON; ignored")
        else:
            if isinstance(overrides, dict):
                merge(config, overrides)
    return config


def merge(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def call_of(message: dict) -> tuple[str | None, dict]:
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    call_id = call.get("id")
    if not isinstance(call_id, str) or not CALL_ID_PATTERN.match(call_id):
        return None, call
    return call_id, call


def find_session(session: Session, call_id: str) -> VoiceSession | None:
    return session.scalar(
        select(VoiceSession).where(VoiceSession.token_hash == sha256(call_token(call_id)))
    )


def answer_call(
    session: Session,
    business: Business,
    settings: Settings,
    message: dict,
    server_url: str,
    now: datetime,
) -> dict:
    """The response to `assistant-request`: an assistant, or a short spoken refusal."""
    call_id, call = call_of(message)
    if call_id is None:
        logger.warning("Vapi assistant request without a usable call id")
        return {"error": DECLINED_MESSAGE}
    if not settings.vapi_enabled:
        logger.info("Declining a Vapi call: Vapi calls are off")
        return {"error": DECLINED_MESSAGE}
    customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
    number = customer.get("number") if isinstance(customer.get("number"), str) else None
    record = find_session(session, call_id)
    if record is None:
        caller_hash = hash_caller(business, number)
        limited = limit_problem(session, business, settings, caller_hash, now)
        if limited is not None:
            logger.info("Declining a Vapi call: %s", limited)
            return {"error": DECLINED_MESSAGE}
        record = VoiceSession(
            id=uuid.uuid4(),
            business_id=business.id,
            mode="live",
            channel="phone",
            provider="vapi",
            model=settings.vapi_model,
            client_hash=caller_hash,
            token_hash=sha256(call_token(call_id)),
            started_at=now,
        )
        session.add(record)
        session.flush()
    # A retried request gets the same assistant for the same call.
    today = now.astimezone(ZoneInfo(business.timezone)).date()
    instructions = build_instructions(session, business, today, "phone", number, greeted=True)
    return {"assistant": assistant(settings, business, instructions, server_url)}


def run_tool_calls(
    session: Session, business: Business, settings: Settings, message: dict, now: datetime
) -> dict:
    """The response to `tool-calls`: one result per call, each a JSON string."""
    call_id, _ = call_of(message)
    tool_calls = message.get("toolCallList")
    if not isinstance(tool_calls, list):
        tool_calls = []
    record = find_session(session, call_id) if call_id else None
    results = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict) or not isinstance(tool_call.get("id"), str):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        name = function.get("name") if isinstance(function.get("name"), str) else ""
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        elif not isinstance(arguments, str):
            arguments = ""
        if record is None or record.provider != "vapi":
            output = {"ok": False, "error": "call_not_found", "message": "Offer a callback."}
        else:
            try:
                outcome = run_tool(
                    session,
                    business.id,
                    record.id,
                    name,
                    call_token(call_id),
                    arguments,
                    settings,
                    now,
                    max_seconds=settings.phone_max_call_seconds,
                )
                session.commit()
                output = {"ok": outcome.ok, **outcome.result}
            except Exception:
                session.rollback()
                logger.exception("Vapi tool %s failed", name)
                output = {"ok": False, "error": "tool_unavailable", "message": "Offer a callback."}
        results.append({"toolCallId": tool_call["id"], "name": name, "result": json.dumps(output)})
    return {"results": results}


def end_call(session: Session, message: dict, now: datetime) -> None:
    call_id, call = call_of(message)
    if call_id is None:
        return
    record = find_session(session, call_id)
    if record is None or record.status == "ended":
        return
    reason = message.get("endedReason") or call.get("endedReason") or ""
    record.status = "ended"
    record.ended_at = now
    record.end_reason = END_REASONS.get(reason, f"vapi:{reason}"[:32] if reason else "ended")
    cost = message.get("cost", call.get("cost"))
    duration = message.get("durationSeconds")
    usage = {}
    if isinstance(cost, int | float):
        usage["cost_usd"] = round(float(cost), 4)
    if isinstance(duration, int | float):
        usage["duration_seconds"] = round(float(duration), 1)
    record.usage = usage or None
