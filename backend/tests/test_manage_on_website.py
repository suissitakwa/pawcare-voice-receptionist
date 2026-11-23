"""Changing or cancelling, on the website, a booking made earlier in the same conversation.

The assistant only puts the change on screen. The visitor's click applies it.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Booking, WebhookEvent
from app.voice import browser_tools
from tests.factories import make_booking
from tests.test_voice_tools import LA, NINE, NOW, booking_args, call, session_record

TWO_PM = datetime(2027, 1, 5, 14, 0, tzinfo=LA)


@pytest.fixture(autouse=True)
def test_mode_and_clock() -> Iterator[None]:
    settings = Settings(voice_mode="test", voice_max_session_seconds=300)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def voice(client: TestClient, db: Session) -> dict:
    return client.post("/api/v1/voice/sessions").json()


def book(client: TestClient, voice: dict, key: str = "voice-confirm-0001", **overrides) -> str:
    prepared = call(client, voice, "prepare_booking", booking_args(**overrides)).json()["prepared"]
    confirmed = client.post(
        "/api/v1/bookings/confirm",
        json={
            "confirmation_token": prepared["confirmation_token"],
            "summary_hash": prepared["summary_hash"],
        },
        headers={"Idempotency-Key": key},
    )
    assert confirmed.status_code == 201
    return confirmed.json()["reference"]


def press(client: TestClient, voice: dict, confirm_token: str, session_token: str | None = None):
    return client.post(
        f"/api/v1/voice/sessions/{voice['session_id']}/manage/confirm",
        json={
            "session_token": session_token or voice["session_token"],
            "confirm_token": confirm_token,
        },
    )


def events(db: Session, event_type: str) -> list[WebhookEvent]:
    return list(db.scalars(select(WebhookEvent).where(WebhookEvent.event_type == event_type)))


def booking(db: Session, reference: str) -> Booking:
    db.expire_all()
    return db.scalar(select(Booking).where(Booking.reference == reference))


def test_the_website_assistant_can_only_show_changes() -> None:
    names = {tool["name"] for tool in browser_tools()}
    assert {"prepare_change", "prepare_cancel"} <= names
    assert not names & {"find_booking", "confirm_change", "cancel_booking", "confirm_booking"}


def test_a_change_is_shown_and_only_the_click_applies_it(client, db, voice) -> None:
    reference = book(client, voice)
    body = call(client, voice, "prepare_change", {"starts_at": TWO_PM.isoformat()}).json()
    assert body["ok"] and body["result"]["status"] == "awaiting_caller_confirmation"
    assert body["result"]["was"]["time"] == "9 AM" and body["result"]["new"]["time"] == "2 PM"
    manage = body["manage"]
    assert manage["action"] == "change" and manage["reference"] == reference
    # The token is for the screen only, never the model.
    assert manage["confirm_token"] not in json.dumps(body["result"])
    assert booking(db, reference).starts_at.astimezone(LA).hour == 9

    pressed = press(client, voice, manage["confirm_token"])
    assert pressed.status_code == 200
    assert pressed.json()["ok"] and pressed.json()["result"]["status"] == "changed"
    moved = booking(db, reference)
    assert moved.starts_at == TWO_PM and moved.status == "confirmed"
    assert len(events(db, "booking.changed")) == 1
    # Used once: a second click finds nothing waiting.
    assert press(client, voice, manage["confirm_token"]).status_code == 409


def test_a_cancellation_is_shown_and_only_the_click_applies_it(client, db, voice) -> None:
    reference = book(client, voice)
    body = call(client, voice, "prepare_cancel").json()
    assert body["ok"] and body["result"]["was"]["time"] == "9 AM"
    assert body["manage"]["action"] == "cancel" and body["manage"]["new"] is None
    assert booking(db, reference).status == "confirmed"

    pressed = press(client, voice, body["manage"]["confirm_token"]).json()
    assert pressed["ok"] and pressed["result"]["status"] == "cancelled"
    gone = booking(db, reference)
    assert gone.status == "cancelled" and gone.cancelled_via == "caller"
    assert gone.cancelled_at == NOW
    assert len(events(db, "booking.cancelled")) == 1


def test_a_wrong_or_stale_token_changes_nothing(client, db, voice) -> None:
    reference = book(client, voice)
    first = call(client, voice, "prepare_cancel").json()["manage"]["confirm_token"]
    # Showing a change replaces the cancellation on screen.
    second = call(client, voice, "prepare_change", {"starts_at": TWO_PM.isoformat()}).json()
    assert press(client, voice, first).status_code == 409
    assert press(client, voice, "x" * 40).status_code == 409
    assert press(client, voice, second["manage"]["confirm_token"], "y" * 40).status_code == 404
    assert booking(db, reference).status == "confirmed"
    assert booking(db, reference).starts_at.astimezone(LA).hour == 9


def test_only_a_booking_from_this_conversation_can_be_changed(client, db, voice) -> None:
    other = make_booking(db, NINE + timedelta(days=1))
    db.commit()
    body = call(client, voice, "prepare_cancel").json()
    assert not body["ok"] and body["result"]["error"] == "no_booking_in_this_conversation"
    assert body["manage"] is None
    assert booking(db, other.reference).status == "confirmed"


def test_the_latest_booking_in_the_conversation_is_the_one_changed(client, db, voice) -> None:
    first = book(client, voice)
    second = book(client, voice, "voice-confirm-0002", starts_at=TWO_PM.isoformat())
    body = call(client, voice, "prepare_cancel").json()
    assert body["manage"]["reference"] == second
    press(client, voice, body["manage"]["confirm_token"])
    assert booking(db, second).status == "cancelled"
    assert booking(db, first).status == "confirmed"
    # Once it is cancelled, the earlier one is the latest still booked.
    assert call(client, voice, "prepare_cancel").json()["manage"]["reference"] == first


def test_an_expired_change_is_not_applied(client, db, voice) -> None:
    reference = book(client, voice)
    token = call(client, voice, "prepare_cancel").json()["manage"]["confirm_token"]
    # A session long enough to outlast the change's 10 minutes.
    longer = Settings(voice_mode="test", voice_max_session_seconds=1800)
    app.dependency_overrides[get_settings] = lambda: longer
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=11)
    pressed = press(client, voice, token).json()
    assert not pressed["ok"] and pressed["result"]["error"] == "change_expired"
    assert booking(db, reference).status == "confirmed"


def test_the_session_record_keeps_nothing_after_the_click(client, db, voice) -> None:
    book(client, voice)
    token = call(client, voice, "prepare_cancel").json()["manage"]["confirm_token"]
    press(client, voice, token)
    assert session_record(db).pending_change is None
