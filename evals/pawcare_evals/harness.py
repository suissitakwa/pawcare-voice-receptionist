"""Runs scripted conversations against the real backend rules in an evaluation database.

Tools execute through the same code as live voice. Confirmation, the slot being taken by
another customer, and backend failure are simulated the way the browser reports them.
"""

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.availability import load_business
from app.booking import confirm_booking
from app.config import Settings
from app.errors import ApiError
from app.models import (
    BlockedPeriod,
    Booking,
    BookingDraft,
    Business,
    CallbackRequest,
    Groomer,
    Service,
    SizeCategory,
    VoiceSession,
)
from app.voice import TOOLS, build_instructions, sha256
from app.voice_tools import run_tool

MAX_TOOL_ROUNDS = 6

# The same words the browser sends. See frontend/src/VoiceAssistant.tsx.
CONFIRMED = (
    "The caller pressed Confirm booking and the booking system confirmed it. "
    "Reference {reference}. Tell them it is booked and read the reference out clearly."
)
TAKEN = (
    "The caller pressed Confirm, but that time was just booked by someone else. "
    "Apologise briefly and offer to check other times."
)
FAILED = "The caller pressed Confirm, but the booking failed. Offer a callback instead."
UNREACHABLE = {
    "error": "tool_unavailable",
    "message": "The booking system could not be reached. Offer a callback instead.",
}


@dataclass
class Record:
    """Everything a scenario produced, for the checks to inspect."""

    entries: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    conflict_at: int | None = None
    confirm_at: int | None = None
    error: str | None = None
    voice_session_id: uuid.UUID | None = None

    def add(self, kind: str, **data) -> int:
        self.entries.append({"kind": kind, **data})
        return len(self.entries) - 1


def reset(session: Session) -> None:
    for model in (Booking, BookingDraft, BlockedPeriod, CallbackRequest, VoiceSession):
        session.execute(delete(model))
    session.commit()


def other_customer_booking(
    session: Session, business_id, starts_at: datetime, minutes: int
) -> None:
    ends_at = starts_at + timedelta(minutes=minutes)
    session.add(
        Booking(
            business_id=business_id,
            groomer_id=session.scalar(select(Groomer.id).where(Groomer.business_id == business_id)),
            service_id=session.scalar(select(Service.id).where(Service.slug == "full-groom")),
            size_category_id=session.scalar(
                select(SizeCategory.id).where(SizeCategory.slug == "medium")
            ),
            starts_at=starts_at,
            ends_at=ends_at,
            occupied_until=ends_at + timedelta(minutes=15),
            status="confirmed",
            reference="PC-OTHER" + secrets.token_hex(2).upper(),
            price_cents=9000,
            owner_name="Another Customer",
            owner_email="other@example.com",
            pet_name="Other",
            summary={},
            idempotency_key="other-" + secrets.token_hex(8),
            request_hash="0" * 64,
        )
    )
    session.commit()


async def run_scenario(session: Session, model, scenario: dict, now: datetime) -> Record:
    reset(session)
    business = session.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    business = load_business(session, business.id)
    token = secrets.token_urlsafe(32)
    voice = VoiceSession(
        id=uuid.uuid4(),
        business_id=business.id,
        mode="live",
        model=getattr(model, "model", "fake"),
        client_hash=sha256("eval"),
        token_hash=sha256(token),
        started_at=now,
    )
    session.add(voice)
    session.commit()
    settings = Settings(voice_mode="live", voice_max_session_seconds=3600)
    record = Record()
    state = {"backend_down": False, "prepared": None}

    async def respond_until_done() -> None:
        for _ in range(MAX_TOOL_ROUNDS):
            reply = await model.respond()
            record.replies.append(
                {
                    "first_output_ms": reply.first_output_ms,
                    "usage": reply.usage,
                    "status": reply.status,
                }
            )
            after = [c["name"] for c in record.tool_calls if c.get("round_open")]
            if reply.text:
                record.add("assistant", text=reply.text, after_tools=after)
            for c in record.tool_calls:
                c["round_open"] = False
            if not reply.calls:
                return
            for call in reply.calls:
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if state["backend_down"]:
                    ok, output, prepared = False, UNREACHABLE, None
                else:
                    outcome = run_tool(
                        session,
                        business.id,
                        voice.id,
                        call["name"],
                        token,
                        call["arguments"],
                        settings,
                        now,
                    )
                    session.commit()
                    ok, output, prepared = outcome.ok, outcome.result, outcome.prepared
                if prepared is not None:
                    state["prepared"] = prepared
                index = record.add("tool", name=call["name"], ok=ok)
                record.tool_calls.append(
                    {
                        "name": call["name"],
                        "args": args,
                        "ok": ok,
                        "result": output,
                        "index": index,
                        "round_open": True,
                    }
                )
                await model.add_tool_output(call["call_id"], output)
        record.error = "too many tool rounds"

    try:
        await model.connect(build_instructions(session, business, now.date()), TOOLS)
        for turn in scenario["turns"]:
            if "caller" in turn:
                record.add("caller", text=turn["caller"])
                await model.add_message("user", turn["caller"])
                await respond_until_done()
            elif turn.get("backend_down"):
                state["backend_down"] = True
            elif "other_customer_takes_slot_at" in turn:
                starts = datetime.fromisoformat(turn["other_customer_takes_slot_at"])
                other_customer_booking(session, business.id, starts, 120)
            elif turn.get("other_customer_takes_slot"):
                prepared = state["prepared"]
                if prepared is not None:
                    minutes = prepared.summary.duration_minutes
                    other_customer_booking(
                        session, business.id, prepared.summary.starts_at, minutes
                    )
            elif turn.get("confirm"):
                prepared = state["prepared"]
                record.confirm_at = record.add("action", text="caller pressed Confirm booking")
                if prepared is None:
                    record.add("action", text="nothing to confirm")
                    continue
                try:
                    confirmation, _ = confirm_booking(
                        session,
                        business,
                        prepared.confirmation_token,
                        prepared.summary_hash,
                        "eval-" + secrets.token_hex(8),
                        now,
                    )
                    session.commit()
                    record.references.append(confirmation.reference)
                    message = CONFIRMED.format(reference=confirmation.reference)
                except ApiError as error:
                    session.rollback()
                    if error.code == "slot_unavailable":
                        record.conflict_at = record.confirm_at
                        message = TAKEN
                    else:
                        message = FAILED
                    record.confirm_at = None
                state["prepared"] = None
                record.add("system", text=message)
                await model.add_message("system", message)
                await respond_until_done()
    except Exception as error:  # noqa: BLE001 - recorded as a scenario error, the run continues
        record.error = f"{type(error).__name__}: {error}"
    finally:
        await model.close()
    record.voice_session_id = voice.id
    return record
