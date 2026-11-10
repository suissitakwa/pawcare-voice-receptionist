"""Phone calls: the signed incoming-call webhook, answering rules, and the server-side tool loop.

OpenAI is replaced by fakes: one records accept, reject and hang-up requests, and a scripted
socket plays the events a call would send.
"""

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_engine
from app.main import app, get_now
from app.models import Booking, Business, VoiceSession
from app.phone import (
    CallControlError,
    InvalidSignature,
    caller_number,
    handle_incoming,
    run_call,
    verify_signature,
)
from app.phone_api import get_call_starter, get_calls

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key-of-32-bytes!!").decode()
CALLER = "+13235550100"


def phone_settings(**overrides) -> Settings:
    values = {
        "voice_mode": "live",
        "openai_api_key": "test-key-not-real",
        "phone_enabled": True,
        "openai_webhook_secret": SECRET,
        "phone_max_call_seconds": 600,
    }
    values.update(overrides)
    return Settings(**values)


class FakeCalls:
    def __init__(self, fail_accept: bool = False) -> None:
        self.accepted: list[tuple[str, dict]] = []
        self.rejected: list[tuple[str, int]] = []
        self.hung_up: list[str] = []
        self.fail_accept = fail_accept

    def accept(self, call_id: str, body: dict) -> None:
        if self.fail_accept:
            raise CallControlError("accept returned HTTP 500")
        self.accepted.append((call_id, body))

    def reject(self, call_id: str, status_code: int) -> None:
        self.rejected.append((call_id, status_code))

    def hangup(self, call_id: str) -> None:
        self.hung_up.append(call_id)


def signed(body: bytes, secret: str = SECRET, sent_at: datetime | None = None) -> dict:
    timestamp = str(int((sent_at or datetime.now(LA)).timestamp()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(key, f"evt_1.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": "evt_1",
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
        "content-type": "application/json",
    }


def incoming(call_id: str = "rtc_abc123", number: str = CALLER) -> dict:
    return {
        "object": "event",
        "id": "evt_1",
        "type": "realtime.call.incoming",
        "data": {
            "call_id": call_id,
            "sip_headers": [
                {"name": "From", "value": f'"Jordan" <sip:{number}@pstn.example.com>;tag=1'},
                {"name": "To", "value": "sip:+18005550199@sip.example.com"},
            ],
        },
    }


@pytest.fixture
def phone(db: Session) -> Iterator[dict]:
    state = {"settings": phone_settings(), "calls": FakeCalls(), "started": []}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_calls] = lambda: state["calls"]
    app.dependency_overrides[get_call_starter] = lambda: lambda *args: state["started"].append(args)
    yield state
    for dependency in (get_settings, get_now, get_calls, get_call_starter):
        app.dependency_overrides.pop(dependency, None)


def post_event(client: TestClient, event: dict, headers: dict | None = None):
    body = json.dumps(event).encode()
    return client.post("/api/v1/phone/openai-events", content=body, headers=headers or signed(body))


def business(db: Session) -> Business:
    return db.scalar(select(Business).where(Business.slug == "pawcare-demo"))


def phone_sessions(db: Session) -> list[VoiceSession]:
    db.expire_all()
    return list(db.scalars(select(VoiceSession).where(VoiceSession.channel == "phone")))


# Signature checks


def test_signature_accepts_a_valid_signature_and_refuses_changes() -> None:
    body = b'{"type":"realtime.call.incoming"}'
    now = datetime.now(LA)
    headers = signed(body, sent_at=now)
    verify_signature(SECRET, headers, body, now)
    with pytest.raises(InvalidSignature):
        verify_signature(SECRET, headers, body + b" ", now)
    other = "whsec_" + base64.b64encode(b"another-key-another-key-another!").decode()
    with pytest.raises(InvalidSignature):
        verify_signature(other, headers, body, now)
    with pytest.raises(InvalidSignature):
        verify_signature(SECRET, headers, body, now + timedelta(minutes=6))
    with pytest.raises(InvalidSignature):
        verify_signature(SECRET, {**headers, "webhook-signature": ""}, body, now)


def test_caller_number_comes_from_the_from_header() -> None:
    assert caller_number(incoming()["data"]["sip_headers"]) == CALLER
    assert caller_number([{"name": "From", "value": "sip:anonymous@example.com"}]) is None
    assert caller_number([]) is None


# The webhook


def test_webhook_is_hidden_until_a_secret_is_set(client: TestClient, phone: dict) -> None:
    phone["settings"] = phone_settings(openai_webhook_secret="")
    assert post_event(client, incoming()).status_code == 404


def test_webhook_refuses_a_bad_signature(client: TestClient, phone: dict, db: Session) -> None:
    body = json.dumps(incoming()).encode()
    headers = signed(body)
    response = client.post("/api/v1/phone/openai-events", content=body + b" ", headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_signature"
    assert phone["calls"].accepted == [] and phone_sessions(db) == []


def test_other_events_are_ignored(client: TestClient, phone: dict) -> None:
    response = post_event(client, {"type": "realtime.call.ended", "data": {}})
    assert response.json() == {"status": "ignored"}


def test_an_incoming_call_is_answered_with_phone_tools(
    client: TestClient, phone: dict, db: Session
) -> None:
    response = post_event(client, incoming())
    assert response.json() == {"status": "answered"}
    [(call_id, body)] = phone["calls"].accepted
    assert call_id == "rtc_abc123"
    assert body["type"] == "realtime" and body["model"] == "gpt-realtime-2.1-mini"
    assert body["audio"]["output"]["voice"] == "marin"
    assert body["audio"]["input"]["turn_detection"]["eagerness"] == "low"
    names = [tool["name"] for tool in body["tools"]]
    assert names == [
        "check_availability",
        "prepare_booking",
        "confirm_booking",
        "create_callback_request",
    ]
    assert "AI assistant" in body["instructions"]
    assert CALLER in body["instructions"]
    assert "on their screen" not in body["instructions"]
    [record] = phone_sessions(db)
    assert record.status == "started" and record.mode == "live"
    # Only a hash of the caller's number is stored.
    assert CALLER not in record.client_hash
    assert phone["started"] == [("rtc_abc123", record.id, record.business_id)]


def test_phone_overrides_set_model_voice_and_turn_taking(
    client: TestClient, phone: dict, db: Session
) -> None:
    phone["settings"] = phone_settings(
        phone_model="gpt-realtime-2.1", phone_voice="cedar", phone_vad_eagerness="medium"
    )
    post_event(client, incoming())
    [(_, body)] = phone["calls"].accepted
    assert body["model"] == "gpt-realtime-2.1"
    assert body["audio"]["output"]["voice"] == "cedar"
    assert body["audio"]["input"]["turn_detection"]["eagerness"] == "medium"
    [record] = phone_sessions(db)
    assert record.model == "gpt-realtime-2.1"


def test_phone_overrides_leave_the_website_voice_alone() -> None:
    settings = phone_settings(phone_model="gpt-realtime-2.1", phone_voice="cedar")
    assert (settings.voice_model, settings.voice_name) == ("gpt-realtime-2.1-mini", "marin")


def test_a_retried_webhook_does_not_answer_twice(
    client: TestClient, phone: dict, db: Session
) -> None:
    post_event(client, incoming())
    post_event(client, incoming())
    assert len(phone["calls"].accepted) == 1
    assert len(phone_sessions(db)) == 1
    assert len(phone["started"]) == 1


def test_calls_are_declined_while_phone_is_off(
    client: TestClient, phone: dict, db: Session
) -> None:
    phone["settings"] = phone_settings(phone_enabled=False)
    assert post_event(client, incoming()).json() == {"status": "declined"}
    assert phone["calls"].rejected == [("rtc_abc123", 603)]
    assert phone_sessions(db) == []


def test_calls_are_declined_when_voice_is_not_live(
    client: TestClient, phone: dict, db: Session
) -> None:
    phone["settings"] = phone_settings(voice_mode="test")
    assert post_event(client, incoming()).json() == {"status": "declined"}
    assert phone["calls"].rejected == [("rtc_abc123", 603)]


def test_the_daily_limit_sends_busy(client: TestClient, phone: dict, db: Session) -> None:
    phone["settings"] = phone_settings(phone_daily_call_limit=1)
    post_event(client, incoming("rtc_first", "+13235550101"))
    assert post_event(client, incoming("rtc_second", "+13235550102")).json() == {
        "status": "declined"
    }
    assert phone["calls"].rejected == [("rtc_second", 486)]


def test_one_caller_is_limited_per_hour(client: TestClient, phone: dict, db: Session) -> None:
    phone["settings"] = phone_settings(phone_calls_per_caller_per_hour=2)
    for index in range(3):
        post_event(client, incoming(f"rtc_{index}"))
    assert len(phone["calls"].accepted) == 2
    assert phone["calls"].rejected == [("rtc_2", 486)]
    # A different caller still gets through.
    assert post_event(client, incoming("rtc_other", "+13235550199")).json() == {
        "status": "answered"
    }


def test_a_failed_accept_ends_the_session(client: TestClient, phone: dict, db: Session) -> None:
    phone["calls"] = FakeCalls(fail_accept=True)
    assert post_event(client, incoming()).json() == {"status": "declined"}
    [record] = phone_sessions(db)
    assert record.status == "ended" and record.end_reason == "accept_failed"
    assert phone["started"] == []


def test_phone_calls_do_not_use_the_website_quota(
    client: TestClient, phone: dict, db: Session
) -> None:
    phone["settings"] = phone_settings(voice_daily_session_limit=1, voice_mode="live")
    post_event(client, incoming())
    phone["settings"] = phone_settings(voice_daily_session_limit=1, voice_mode="test")
    assert client.post("/api/v1/voice/sessions").status_code == 201


# The call itself


class ScriptedSocket:
    """Plays server events. Each `respond` step waits for the previous replies to be sent."""

    def __init__(self, events: list[dict], hang: bool = False) -> None:
        self.events = events
        self.sent: list[dict] = []
        self.hang = hang

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self.play()

    async def play(self):
        for event in self.events:
            yield json.dumps(event)
        if self.hang:
            await asyncio.sleep(3600)


def tool_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "type": "response.done",
        "response": {
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "output": [
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": call_id,
                    "arguments": json.dumps(args),
                }
            ],
        },
    }


def outputs(socket: ScriptedSocket) -> list[dict]:
    return [
        json.loads(message["item"]["output"])
        for message in socket.sent
        if message["type"] == "conversation.item.create"
    ]


def answered(db: Session, settings: Settings, call_id: str = "rtc_call") -> VoiceSession:
    data = incoming(call_id)["data"]
    record = handle_incoming(db, business(db), settings, data, FakeCalls(), NOW)
    db.commit()
    assert record is not None
    return record


def play(
    db: Session, socket: ScriptedSocket, settings: Settings | None = None, calls=None
) -> tuple[str, VoiceSession]:
    settings = settings or phone_settings()
    record = answered(db, settings)
    reason = asyncio.run(
        run_call(
            "rtc_call",
            record.id,
            record.business_id,
            settings,
            get_engine,
            lambda: NOW + timedelta(minutes=2),
            calls or FakeCalls(),
            connect=lambda *_: socket,
        )
    )
    db.expire_all()
    return reason, db.get(VoiceSession, record.id)


BOOKING = {
    "service": "full-groom",
    "size": "medium",
    "starts_at": NINE.isoformat(),
    "owner_name": "Jordan",
    "pet_name": "Biscuit",
    "owner_phone": CALLER,
    "details_read_back": True,
}


def test_a_caller_books_by_saying_yes(db: Session) -> None:
    socket = ScriptedSocket(
        [
            {"type": "session.created"},
            tool_call(
                "check_availability",
                {"service": "full-groom", "size": "medium", "date": "2027-01-05"},
                "c1",
            ),
            tool_call("prepare_booking", BOOKING, "c2"),
            tool_call("confirm_booking", {"caller_confirmed": True}, "c3"),
        ]
    )
    reason, record = play(db, socket)
    assert reason == "caller_hung_up"
    # The model is asked to speak first, then to answer after each tool result.
    assert socket.sent[0] == {"type": "response.create"}
    availability, prepared, confirmed = outputs(socket)
    assert availability["ok"] and availability["times"][0]["label"] == "9 AM"
    assert prepared["ok"] and prepared["status"] == "awaiting_caller_confirmation"
    # The confirmation token never reaches the model.
    assert "confirmation_token" not in json.dumps(socket.sent)
    assert confirmed["ok"] and confirmed["status"] == "booked"
    booking = db.scalar(select(Booking))
    assert booking.reference == confirmed["reference"]
    assert booking.voice_session_id == record.id
    assert record.booking_reference == booking.reference
    assert record.status == "ended" and record.end_reason == "caller_hung_up"
    assert record.tool_calls == 3 and record.tool_failures == 0
    assert record.usage["input_tokens"] == 300 and record.usage["responses"] == 3


def test_confirming_twice_returns_the_same_booking(db: Session) -> None:
    socket = ScriptedSocket(
        [
            tool_call("prepare_booking", BOOKING, "c1"),
            tool_call("confirm_booking", {"caller_confirmed": True}, "c2"),
            tool_call("confirm_booking", {"caller_confirmed": True}, "c3"),
        ]
    )
    play(db, socket)
    _, first, second = outputs(socket)
    assert first["reference"] == second["reference"]
    assert db.scalar(select(func.count()).select_from(Booking)) == 1


def test_nothing_is_booked_without_a_spoken_yes(db: Session) -> None:
    socket = ScriptedSocket(
        [
            tool_call("confirm_booking", {"caller_confirmed": True}, "c1"),
            tool_call("prepare_booking", BOOKING, "c2"),
            tool_call("confirm_booking", {"caller_confirmed": False}, "c3"),
            tool_call("confirm_booking", {"caller_confirmed": True, "starts_at": "anything"}, "c4"),
        ]
    )
    _, record = play(db, socket)
    early, _, refused, extra = outputs(socket)
    assert early["error"] == "nothing_to_confirm"
    assert refused["error"] == "caller_confirmation_required"
    assert extra["error"] == "caller_confirmation_required"
    assert db.scalar(select(func.count()).select_from(Booking)) == 0
    assert record.tool_failures == 3


def test_a_browser_session_cannot_use_confirm_booking(client: TestClient, db: Session) -> None:
    settings = Settings(voice_mode="test")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_now] = lambda: NOW
    try:
        voice = client.post("/api/v1/voice/sessions").json()
        response = client.post(
            f"/api/v1/voice/sessions/{voice['session_id']}/tools/confirm_booking",
            json={
                "session_token": voice["session_token"],
                "arguments": json.dumps({"caller_confirmed": True}),
            },
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_now, None)
    assert response.json()["result"]["error"] == "unknown_tool"


def test_a_call_over_the_time_limit_is_hung_up(db: Session) -> None:
    calls = FakeCalls()
    socket = ScriptedSocket([{"type": "session.created"}], hang=True)
    reason, record = play(db, socket, phone_settings(phone_max_call_seconds=1), calls)
    assert reason == "time_limit"
    assert calls.hung_up == ["rtc_call"]
    assert record.end_reason == "time_limit"


def test_a_lost_connection_still_ends_the_session(db: Session) -> None:
    class Broken(ScriptedSocket):
        async def play(self):
            yield json.dumps({"type": "session.created"})
            raise ConnectionResetError

    reason, record = play(db, Broken([]))
    assert reason == "connection_error"
    assert record.status == "ended" and record.end_reason == "connection_error"


def test_staff_metrics_count_phone_calls(client: TestClient, phone: dict, db: Session) -> None:
    from argon2 import PasswordHasher

    from app.models import StaffUser

    post_event(client, incoming())
    db.add(
        StaffUser(
            business_id=business(db).id,
            email="maya@pawcare.example",
            display_name="Maya",
            password_hash=PasswordHasher().hash("correct-horse-battery"),
        )
    )
    db.commit()
    token = client.post(
        "/api/v1/staff/login",
        json={"email": "maya@pawcare.example", "password": "correct-horse-battery"},
    ).json()["token"]
    metrics = client.get(
        "/api/v1/staff/metrics", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert metrics["phone_calls_last_7_days"] == 1
    assert metrics["voice_sessions_last_7_days"] == 1
