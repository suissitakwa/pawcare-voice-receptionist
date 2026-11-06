"""Assistant tools, called the way the browser relays them. No provider is involved."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Booking, BookingDraft, Business, CallbackRequest, StaffUser, VoiceSession
from app.voice import MAX_TOOL_CALLS, TOOLS

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)


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


def call(client: TestClient, voice: dict, name: str, args: dict | str | None = None, token=None):
    arguments = args if isinstance(args, str) else json.dumps(args or {})
    return client.post(
        f"/api/v1/voice/sessions/{voice['session_id']}/tools/{name}",
        json={"session_token": token or voice["session_token"], "arguments": arguments},
    )


def booking_args(**overrides) -> dict:
    args = {
        "service": "full-groom",
        "size": "medium",
        "starts_at": NINE.isoformat(),
        "owner_name": "Jordan",
        "pet_name": "Biscuit",
        "owner_phone": "(323) 555-0100",
        "details_read_back": True,
    }
    args.update(overrides)
    return args


def session_record(db: Session) -> VoiceSession:
    db.expire_all()
    return db.scalar(select(VoiceSession))


# Definitions sent to the model


def test_the_model_is_offered_no_way_to_confirm_a_booking() -> None:
    names = {tool["name"] for tool in TOOLS}
    assert names == {"check_availability", "prepare_booking", "create_callback_request"}


def test_tool_schemas_reject_extra_fields() -> None:
    assert all(tool["parameters"]["additionalProperties"] is False for tool in TOOLS)


# check_availability


def test_availability_returns_real_times_with_labels(client, voice) -> None:
    body = call(
        client,
        voice,
        "check_availability",
        {"service": "full-groom", "size": "medium", "date": "2027-01-05"},
    ).json()
    assert body["ok"] is True
    result = body["result"]
    assert result["duration_minutes"] == 120
    assert result["price_dollars"] == 90
    assert result["times"][0]["label"] == "9 AM"
    assert datetime.fromisoformat(result["times"][0]["starts_at"]) == NINE
    assert "12:30 PM" in [t["label"] for t in result["times"]]


def test_availability_includes_the_whole_day_so_the_model_never_guesses(client, voice) -> None:
    body = call(
        client,
        voice,
        "check_availability",
        {"service": "nail-and-paw", "size": "large", "date": "2027-01-07"},
    ).json()
    labels = [t["label"] for t in body["result"]["times"]]
    assert labels[0] == "9 AM"
    assert "2:30 PM" in labels
    assert labels[-1] == "4:30 PM"
    assert body["result"]["more_times_available"] is False
    assert "requested_time" not in body["result"]


@pytest.mark.parametrize(
    ("preferred", "available"), [("09:00", True), ("14:30", True), ("08:00", False)]
)
def test_a_named_time_is_answered_directly(client, voice, preferred, available) -> None:
    args = {"service": "nail-and-paw", "size": "large", "date": "2027-01-07"}
    body = call(client, voice, "check_availability", {**args, "preferred_time": preferred})
    requested = body.json()["result"]["requested_time"]
    assert requested["available"] is available
    assert (requested["starts_at"] is not None) is available


def test_invalid_arguments_name_the_field_but_not_the_value(client, voice) -> None:
    args = {"service": "full-groom", "size": "medium", "date": "2027-01-07", "time": "9 AM"}
    result = call(client, voice, "check_availability", args).json()["result"]
    assert result["error"] == "invalid_arguments"
    assert "time" in result["message"]
    assert "9 AM" not in result["message"]


def test_availability_reports_closed_days(client, voice) -> None:
    result = call(
        client,
        voice,
        "check_availability",
        {"service": "full-groom", "size": "medium", "date": "2027-01-11"},
    ).json()["result"]
    assert result["closed"] is True
    assert result["times"] == []


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (
            {"service": "full-groom", "size": "extra-large", "date": "2027-01-05"},
            "staff_assessment_required",
        ),
        ({"service": "full-groom", "size": "medium", "date": "2027-01-01"}, "date_in_past"),
        ({"service": "spa", "size": "medium", "date": "2027-01-05"}, "unknown_service"),
        ({"service": "full-groom", "size": "medium", "date": "next tuesday"}, "invalid_arguments"),
        (
            {"service": "full-groom", "size": "medium", "date": "2027-01-05", "price": 1},
            "invalid_arguments",
        ),
        ("not json", "invalid_arguments"),
        ("[1, 2]", "invalid_arguments"),
    ],
)
def test_bad_or_disallowed_requests_become_failures_the_assistant_can_explain(
    client, db, voice, args, code
) -> None:
    body = call(client, voice, "check_availability", args).json()
    assert body == {
        "ok": False,
        "result": {"error": code, "message": body["result"]["message"]},
        "prepared": None,
    }
    assert session_record(db).tool_failures == 1


# prepare_booking and the visible confirmation


def test_prepare_shows_a_summary_but_books_nothing(client, db, voice) -> None:
    response = call(client, voice, "prepare_booking", booking_args())
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["status"] == "awaiting_caller_confirmation"
    assert body["result"]["time"] == "9 AM"
    assert body["result"]["price_dollars"] == 90
    token = body["prepared"]["confirmation_token"]
    assert token not in json.dumps(body["result"])
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Booking)) == 0
    assert db.scalar(select(BookingDraft.voice_session_id)) is not None


@pytest.mark.parametrize("read_back", [False, None, "yes", 1])
def test_prepare_requires_the_details_to_have_been_read_back(client, db, voice, read_back) -> None:
    args = booking_args(details_read_back=read_back)
    if read_back is None:
        del args["details_read_back"]
    body = call(client, voice, "prepare_booking", args).json()
    assert body["ok"] is False
    assert body["result"]["error"] == "read_back_required"
    assert body["prepared"] is None
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(BookingDraft)) == 0


def test_the_model_is_told_it_must_read_back_details() -> None:
    prepare_tool = next(tool for tool in TOOLS if tool["name"] == "prepare_booking")
    assert "details_read_back" in prepare_tool["parameters"]["required"]


def test_prepare_rejects_a_time_the_rules_do_not_allow(client, db, voice) -> None:
    body = call(
        client,
        voice,
        "prepare_booking",
        booking_args(starts_at=datetime(2027, 1, 5, 11, 0, tzinfo=LA).isoformat()),
    ).json()
    assert body["ok"] is False
    assert body["result"]["error"] == "slot_unavailable"


def test_prepare_validates_contact_details(client, voice) -> None:
    body = call(client, voice, "prepare_booking", booking_args(owner_phone="call me maybe")).json()
    assert body["ok"] is False
    assert body["result"]["error"] == "invalid_arguments"
    assert "call me maybe" not in json.dumps(body)


def test_clicking_confirm_books_it_as_a_voice_booking(client, db, voice) -> None:
    prepared = call(client, voice, "prepare_booking", booking_args()).json()["prepared"]
    confirmed = client.post(
        "/api/v1/bookings/confirm",
        json={
            "confirmation_token": prepared["confirmation_token"],
            "summary_hash": prepared["summary_hash"],
        },
        headers={"Idempotency-Key": "voice-confirm-0001"},
    )
    assert confirmed.status_code == 201
    reference = confirmed.json()["reference"]
    db.expire_all()
    booking = db.scalar(select(Booking))
    assert booking.voice_session_id == session_record(db).id
    assert session_record(db).booking_reference == reference


# create_callback_request


def test_callback_is_saved_for_staff(client, db, voice) -> None:
    body = call(
        client,
        voice,
        "create_callback_request",
        {"name": "Sam", "phone": "(323) 555-0101", "reason": "health", "note": "Skin rash"},
    ).json()
    assert body == {
        "ok": True,
        "result": {"status": "callback_requested", "message": "Staff will contact the caller."},
        "prepared": None,
    }
    callback = db.scalar(select(CallbackRequest))
    assert (callback.name, callback.reason, callback.status) == ("Sam", "health", "open")


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"name": "Sam", "reason": "health"}, "contact_required"),
        ({"name": "Sam", "phone": "nope", "reason": "health"}, "invalid_phone"),
        ({"name": "Sam", "email": "nope", "reason": "health"}, "invalid_email"),
        ({"name": "Sam", "phone": "(323) 555-0101", "reason": "refund"}, "invalid_arguments"),
    ],
)
def test_callback_needs_valid_contact_and_reason(client, db, voice, args, code) -> None:
    assert call(client, voice, "create_callback_request", args).json()["result"]["error"] == code
    assert db.scalar(select(func.count()).select_from(CallbackRequest)) == 0


# Who may call tools, and for how long


def test_unknown_tools_including_confirm_are_refused(client, db, voice) -> None:
    body = call(client, voice, "confirm_booking", {"confirmation_token": "x"}).json()
    assert body["result"]["error"] == "unknown_tool"
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


def test_a_wrong_session_token_is_rejected(client, voice) -> None:
    response = call(client, voice, "check_availability", {}, token="x" * 43)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_session_not_found"


def test_tools_stop_working_when_the_session_ends(client, voice) -> None:
    client.post(
        f"/api/v1/voice/sessions/{voice['session_id']}/end",
        json={"session_token": voice["session_token"], "reason": "user_ended"},
    )
    response = call(client, voice, "check_availability", {})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "voice_session_closed"


def test_tools_stop_working_after_the_time_limit(client, voice) -> None:
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(seconds=300 + 61)
    assert call(client, voice, "check_availability", {}).status_code == 403


def test_each_session_has_a_call_budget(client, db, voice) -> None:
    record = session_record(db)
    record.tool_calls = MAX_TOOL_CALLS
    db.commit()
    response = call(client, voice, "check_availability", {})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_tool_calls"


# Staff see callbacks and voice figures


@pytest.fixture
def staff_headers(client, db) -> dict:
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    db.add(
        StaffUser(
            business_id=business_id,
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
    return {"Authorization": f"Bearer {token}"}


def test_staff_can_list_and_close_callbacks(client, db, voice, staff_headers) -> None:
    call(
        client,
        voice,
        "create_callback_request",
        {"name": "Sam", "email": "sam@example.com", "reason": "large_dog"},
    )
    callbacks = client.get("/api/v1/staff/callbacks", headers=staff_headers).json()
    assert [c["reason"] for c in callbacks] == ["large_dog"]
    done = client.post(
        f"/api/v1/staff/callbacks/{callbacks[0]['id']}/done", headers=staff_headers
    ).json()
    assert done["status"] == "done"
    assert client.get("/api/v1/staff/callbacks", headers=staff_headers).json() == []
    assert (
        len(
            client.get(
                "/api/v1/staff/callbacks", params={"status": "all"}, headers=staff_headers
            ).json()
        )
        == 1
    )


def test_callbacks_require_staff_sign_in(client) -> None:
    assert client.get("/api/v1/staff/callbacks").status_code == 401


def test_metrics_count_voice_activity(client, db, voice, staff_headers) -> None:
    call(
        client,
        voice,
        "create_callback_request",
        {"name": "Sam", "email": "sam@example.com", "reason": "other"},
    )
    call(client, voice, "check_availability", "not json")
    prepared = call(client, voice, "prepare_booking", booking_args()).json()["prepared"]
    client.post(
        "/api/v1/bookings/confirm",
        json={
            "confirmation_token": prepared["confirmation_token"],
            "summary_hash": prepared["summary_hash"],
        },
        headers={"Idempotency-Key": "voice-metrics-0001"},
    )
    metrics = client.get("/api/v1/staff/metrics", headers=staff_headers).json()
    assert metrics["voice_sessions_last_7_days"] == 1
    assert metrics["voice_bookings_last_7_days"] == 1
    assert metrics["open_callbacks"] == 1
    assert metrics["tool_failures_last_7_days"] == 1
    bookings = client.get("/api/v1/staff/bookings", headers=staff_headers).json()
    assert bookings[0]["source"] == "voice"
