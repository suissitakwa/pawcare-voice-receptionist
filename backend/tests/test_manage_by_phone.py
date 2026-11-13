"""Changing and cancelling a booking on a phone call, with OpenAI replaced by scripted events."""

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.booking import confirm_booking, prepare_booking
from app.config import Settings, get_settings
from app.main import app, get_now
from app.manage import MAX_LOOKUP_FAILURES, contact_matches, normalize_reference
from app.models import Booking, WebhookEvent
from app.schemas import PrepareBookingRequest
from tests.factories import make_booking
from tests.test_phone import (
    CALLER,
    LA,
    NINE,
    NOW,
    ScriptedSocket,
    business,
    outputs,
    play,
    tool_call,
)

TWO_PM = datetime(2027, 1, 5, 14, 0, tzinfo=LA)


def existing_booking(db: Session) -> Booking:
    """A booking made earlier through the normal two-step flow, so it has a real summary."""
    shop = business(db)
    prepared = prepare_booking(
        db,
        shop,
        PrepareBookingRequest(
            service="full-groom",
            size="medium",
            starts_at=NINE,
            owner_name="Jordan",
            pet_name="Biscuit",
            owner_phone=CALLER,
            owner_email="jordan@example.com",
        ),
        NOW - timedelta(days=1),
    )
    confirmation, _ = confirm_booking(
        db,
        shop,
        prepared.confirmation_token,
        prepared.summary_hash,
        "earlier-booking",
        NOW - timedelta(days=1),
    )
    db.commit()
    return db.scalar(select(Booking).where(Booking.reference == confirmation.reference))


def spoken(reference: str) -> str:
    """How a caller might say it: lower case, spaced out, no dash."""
    return " ".join(reference.replace("-", "").lower())


def find(reference: str, contact: str = CALLER, call: str = "f1") -> dict:
    return tool_call("find_booking", {"reference": reference, "contact": contact}, call)


def events(db: Session, event_type: str) -> list[WebhookEvent]:
    return list(db.scalars(select(WebhookEvent).where(WebhookEvent.event_type == event_type)))


def test_references_and_contacts_are_matched_as_spoken() -> None:
    assert normalize_reference("pc 7 7 p f q n w 8") == "PC-77PFQNW8"
    assert normalize_reference("PC-77PFQNW8") == "PC-77PFQNW8"
    booking = Booking(owner_phone="+13235550100", owner_email="Jordan@Example.com")
    assert contact_matches(booking, "(323) 555-0100")
    assert contact_matches(booking, "jordan@example.com")
    assert not contact_matches(booking, "555-0100")
    assert not contact_matches(booking, "someone@example.com")


def test_a_caller_moves_a_booking_by_saying_yes(db: Session) -> None:
    booking = existing_booking(db)
    socket = ScriptedSocket(
        [
            find(spoken(booking.reference)),
            tool_call("prepare_change", {"starts_at": TWO_PM.isoformat()}, "c2"),
            tool_call("confirm_change", {"caller_confirmed": True}, "c3"),
        ]
    )
    _, record = play(db, socket)
    found, prepared, changed = outputs(socket)
    assert found["ok"] and found["time"] == "9 AM" and found["pet_name"] == "Biscuit"
    assert prepared["was"]["time"] == "9 AM" and prepared["new"]["time"] == "2 PM"
    assert changed["ok"] and changed["status"] == "changed"
    assert changed["reference"] == booking.reference
    db.expire_all()
    moved = db.get(Booking, booking.id)
    assert moved.starts_at == TWO_PM and moved.status == "confirmed"
    assert moved.changed_at is not None
    assert moved.summary["local_start"].startswith("2027-01-05T14:00")
    assert record.pending_change is None
    [event] = events(db, "booking.changed")
    assert event.payload["data"]["local_start"].startswith("2027-01-05T14:00")


def test_a_caller_corrects_a_misheard_name(db: Session) -> None:
    booking = existing_booking(db)
    socket = ScriptedSocket(
        [
            find(booking.reference),
            tool_call("prepare_change", {}, "c2"),
            tool_call("prepare_change", {"owner_name": "Takwa"}, "c3"),
            tool_call("confirm_change", {"caller_confirmed": True}, "c4"),
        ]
    )
    play(db, socket)
    _, empty, prepared, changed = outputs(socket)
    assert empty["error"] == "nothing_to_change"
    assert prepared["was"]["owner_name"] == "Jordan"
    assert prepared["new"]["owner_name"] == "Takwa" and prepared["new"]["time"] == "9 AM"
    assert changed["ok"], changed
    db.expire_all()
    fixed = db.get(Booking, booking.id)
    assert fixed.owner_name == "Takwa" and fixed.summary["owner_name"] == "Takwa"
    assert fixed.pet_name == "Biscuit" and fixed.starts_at == NINE
    assert fixed.calendar_dirty
    [event] = events(db, "booking.changed")
    assert event.payload["data"]["owner_name"] == "Takwa"


def test_a_booking_can_move_into_time_it_already_holds(db: Session) -> None:
    booking = existing_booking(db)
    later = NINE + timedelta(minutes=30)
    socket = ScriptedSocket(
        [
            find(booking.reference),
            tool_call("prepare_change", {"starts_at": later.isoformat()}, "c2"),
            tool_call("confirm_change", {"caller_confirmed": True}, "c3"),
        ]
    )
    play(db, socket)
    *_, changed = outputs(socket)
    assert changed["ok"], changed
    db.expire_all()
    assert db.get(Booking, booking.id).starts_at == later


def test_a_change_into_someone_elses_time_is_refused(db: Session) -> None:
    booking = existing_booking(db)
    make_booking(db, TWO_PM)
    socket = ScriptedSocket(
        [
            find(booking.reference),
            tool_call("prepare_change", {"starts_at": TWO_PM.isoformat()}, "c2"),
            tool_call("confirm_change", {"caller_confirmed": True}, "c3"),
        ]
    )
    play(db, socket)
    _, refused, nothing = outputs(socket)
    assert refused["error"] == "slot_unavailable"
    assert nothing["error"] == "nothing_to_confirm"
    db.expire_all()
    assert db.get(Booking, booking.id).starts_at == NINE


def test_nothing_changes_without_finding_the_booking_or_a_yes(db: Session) -> None:
    booking = existing_booking(db)
    socket = ScriptedSocket(
        [
            tool_call("prepare_change", {"starts_at": TWO_PM.isoformat()}, "c1"),
            tool_call("cancel_booking", {"caller_confirmed": True}, "c2"),
            find(booking.reference),
            tool_call("prepare_change", {"starts_at": TWO_PM.isoformat()}, "c4"),
            tool_call("confirm_change", {"caller_confirmed": False}, "c5"),
            tool_call("cancel_booking", {"caller_confirmed": True, "reference": "X"}, "c6"),
        ]
    )
    play(db, socket)
    early_change, early_cancel, _, _, no_yes, extra = outputs(socket)
    assert early_change["error"] == early_cancel["error"] == "booking_not_identified"
    assert no_yes["error"] == extra["error"] == "caller_confirmation_required"
    db.expire_all()
    unchanged = db.get(Booking, booking.id)
    assert unchanged.starts_at == NINE and unchanged.status == "confirmed"


def test_a_caller_cancels_by_saying_yes(db: Session) -> None:
    booking = existing_booking(db)
    socket = ScriptedSocket(
        [
            find(booking.reference, contact="Jordan@example.com"),
            tool_call("cancel_booking", {"caller_confirmed": True}, "c2"),
            tool_call("cancel_booking", {"caller_confirmed": True}, "c3"),
        ]
    )
    play(db, socket)
    _, cancelled, again = outputs(socket)
    assert cancelled["ok"] and cancelled["status"] == "cancelled"
    assert again["ok"] and again["status"] == "cancelled"
    db.expire_all()
    gone = db.get(Booking, booking.id)
    assert gone.status == "cancelled" and gone.cancelled_via == "caller"
    assert gone.cancelled_at == NOW + timedelta(minutes=2)
    assert len(events(db, "booking.cancelled")) == 1


def test_wrong_details_never_reveal_a_booking_and_guessing_stops(db: Session) -> None:
    booking = existing_booking(db)
    attempts = [find("PC-AAAAAAAA", call="w1"), find(booking.reference, "+15555550123", "w2")]
    attempts += [find("PC-BBBBBBBB", call="w3"), find(booking.reference, call="w4")]
    socket = ScriptedSocket(attempts)
    _, record = play(db, socket)
    unknown, wrong_contact, third, blocked = outputs(socket)
    # An unknown reference and a wrong contact get the same answer.
    assert unknown == wrong_contact and unknown["error"] == "booking_not_found"
    assert third["error"] == "booking_not_found"
    assert blocked["error"] == "too_many_attempts"
    assert record.lookup_failures == MAX_LOOKUP_FAILURES
    assert record.managed_booking_id is None


def test_a_cancelled_booking_cannot_be_managed(db: Session) -> None:
    booking = existing_booking(db)
    booking.status = "cancelled"
    db.commit()
    socket = ScriptedSocket([find(booking.reference)])
    play(db, socket)
    [found] = outputs(socket)
    assert found["error"] == "already_cancelled"


def test_a_browser_session_cannot_manage_bookings(client: TestClient, db: Session) -> None:
    booking = existing_booking(db)
    app.dependency_overrides[get_settings] = lambda: Settings(voice_mode="test")
    app.dependency_overrides[get_now] = lambda: NOW
    try:
        voice = client.post("/api/v1/voice/sessions").json()
        response = client.post(
            f"/api/v1/voice/sessions/{voice['session_id']}/tools/find_booking",
            json={
                "session_token": voice["session_token"],
                "arguments": json.dumps({"reference": booking.reference, "contact": CALLER}),
            },
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_now, None)
    assert response.json()["result"]["error"] == "unknown_tool"
