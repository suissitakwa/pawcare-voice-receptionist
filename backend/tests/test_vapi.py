"""Phone calls through Vapi: the assistant request, tool calls and the end-of-call report.

Vapi is replaced by requests shaped like the ones it sends to the server URL.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Booking, VoiceSession

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
SECRET = "a-shared-vapi-secret-for-tests-only"
CALLER = "+13235550100"
URL = "/api/v1/vapi/events"
AUTH = {"Authorization": f"Bearer {SECRET}"}


def vapi_settings(**overrides) -> Settings:
    values = {"vapi_enabled": True, "vapi_secret": SECRET}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def vapi() -> Iterator[dict]:
    state = {"settings": vapi_settings()}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_now] = lambda: NOW
    yield state
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


def call(call_id: str = "call-1", number: str = CALLER) -> dict:
    return {"id": call_id, "customer": {"number": number}}


def send(client: TestClient, message: dict, headers: dict | None = None):
    return client.post(URL, json={"message": message}, headers=AUTH if headers is None else headers)


def assistant_request(client: TestClient, call_id: str = "call-1", number: str = CALLER) -> dict:
    return send(client, {"type": "assistant-request", "call": call(call_id, number)}).json()


def tools(client: TestClient, *calls: tuple[str, dict], call_id: str = "call-1") -> list[dict]:
    message = {
        "type": "tool-calls",
        "call": call(call_id),
        "toolCallList": [
            {"id": f"t{index}", "type": "function", "function": {"name": name, "arguments": args}}
            for index, (name, args) in enumerate(calls)
        ],
    }
    results = send(client, message).json()["results"]
    return [json.loads(result["result"]) for result in results]


def sessions(db: Session) -> list[VoiceSession]:
    db.expire_all()
    return list(db.scalars(select(VoiceSession).where(VoiceSession.provider == "vapi")))


# Authentication


def test_the_endpoint_is_hidden_until_a_secret_is_set(client: TestClient, vapi) -> None:
    vapi["settings"] = Settings()
    assert send(client, {"type": "assistant-request"}).status_code == 404


def test_requests_without_the_secret_are_refused(client: TestClient, vapi, db) -> None:
    assert send(client, {"type": "assistant-request"}, headers={}).status_code == 401
    wrong = {"Authorization": "Bearer not-the-secret"}
    assert send(client, {"type": "assistant-request"}, headers=wrong).status_code == 401
    assert sessions(db) == []


def test_the_older_x_vapi_secret_header_works(client: TestClient, vapi) -> None:
    response = send(
        client, {"type": "assistant-request", "call": call()}, headers={"X-Vapi-Secret": SECRET}
    )
    assert "assistant" in response.json()


# Answering


def test_a_call_gets_the_phone_assistant(client: TestClient, vapi, db) -> None:
    assistant = assistant_request(client)["assistant"]
    assert assistant["firstMessage"].startswith("Hi, thanks for calling")
    assert "AI assistant" in assistant["firstMessage"]
    assert assistant["voice"] == {"provider": "11labs", "voiceId": "21m00Tcm4TlvDq8ikWAM"}
    assert assistant["transcriber"]["provider"] == "deepgram"
    assert assistant["maxDurationSeconds"] == 600
    model = assistant["model"]
    assert model["model"] == "gpt-4.1-mini"
    names = [tool["function"]["name"] for tool in model["tools"]]
    assert "confirm_booking" in names and "find_booking" in names
    instructions = model["messages"][0]["content"]
    assert CALLER in instructions and "already greeted" in instructions
    assert "ask them to spell it" in instructions
    assert "If the caller says a name is wrong" in instructions
    # Tool calls come back to this endpoint, with the secret.
    assert assistant["server"]["url"].endswith(URL)
    assert assistant["server"]["headers"]["Authorization"] == f"Bearer {SECRET}"
    [record] = sessions(db)
    assert record.channel == "phone" and record.status == "started"
    assert CALLER not in record.client_hash


def test_a_retried_request_does_not_start_a_second_session(client: TestClient, vapi, db) -> None:
    assistant_request(client)
    assistant_request(client)
    assert len(sessions(db)) == 1


def test_calls_are_refused_while_vapi_is_off(client: TestClient, vapi, db) -> None:
    vapi["settings"] = vapi_settings(vapi_enabled=False)
    assert "can't take calls" in assistant_request(client)["error"]
    assert sessions(db) == []


def test_one_caller_is_limited_per_hour(client: TestClient, vapi, db) -> None:
    vapi["settings"] = vapi_settings(phone_calls_per_caller_per_hour=1)
    assert "assistant" in assistant_request(client, "call-1")
    assert "error" in assistant_request(client, "call-2")
    assert "assistant" in assistant_request(client, "call-3", number="+13235550111")


def test_overrides_tune_the_assistant(client: TestClient, vapi) -> None:
    overrides = {"voice": {"voiceId": "another-voice"}, "startSpeakingPlan": {"waitSeconds": 0.8}}
    vapi["settings"] = vapi_settings(vapi_assistant_overrides=json.dumps(overrides))
    assistant = assistant_request(client)["assistant"]
    assert assistant["voice"] == {"provider": "11labs", "voiceId": "another-voice"}
    assert assistant["startSpeakingPlan"] == {"waitSeconds": 0.8}


# Tools


BOOKING = {
    "service": "full-groom",
    "size": "medium",
    "starts_at": NINE.isoformat(),
    "owner_name": "Jordan",
    "pet_name": "Biscuit",
    "owner_phone": CALLER,
    "details_read_back": True,
}


def test_a_caller_books_by_saying_yes(client: TestClient, vapi, db) -> None:
    assistant_request(client)
    availability, prepared = tools(
        client,
        ("check_availability", {"service": "full-groom", "size": "medium", "date": "2027-01-05"}),
        ("prepare_booking", BOOKING),
    )
    assert availability["ok"] and availability["times"][0]["label"] == "9 AM"
    assert prepared["status"] == "awaiting_caller_confirmation"
    assert "confirmation_token" not in json.dumps(prepared)
    [booked] = tools(client, ("confirm_booking", {"caller_confirmed": True}))
    assert booked["ok"] and booked["status"] == "booked"
    [again] = tools(client, ("confirm_booking", {"caller_confirmed": True}))
    assert again["reference"] == booked["reference"]
    assert db.scalar(select(func.count()).select_from(Booking)) == 1
    [record] = sessions(db)
    assert record.booking_reference == booked["reference"]
    assert record.tool_calls == 4


def test_nothing_is_booked_without_a_yes(client: TestClient, vapi, db) -> None:
    assistant_request(client)
    tools(client, ("prepare_booking", BOOKING))
    [refused] = tools(client, ("confirm_booking", {"caller_confirmed": False}))
    assert refused["error"] == "caller_confirmation_required"
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


def test_tool_results_match_their_call_ids(client: TestClient, vapi) -> None:
    assistant_request(client)
    message = {
        "type": "tool-calls",
        "call": call(),
        "toolCallList": [
            {"id": "abc", "type": "function", "function": {"name": "nope", "arguments": {}}}
        ],
    }
    [result] = send(client, message).json()["results"]
    assert result["toolCallId"] == "abc" and result["name"] == "nope"
    assert json.loads(result["result"])["error"] == "unknown_tool"


def test_tools_for_an_unknown_call_do_nothing(client: TestClient, vapi, db) -> None:
    [result] = tools(client, ("prepare_booking", BOOKING), call_id="never-answered")
    assert result["error"] == "call_not_found"
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


# Ending


def test_the_end_of_call_report_ends_the_session_with_its_cost(
    client: TestClient, vapi, db
) -> None:
    assistant_request(client)
    later = NOW + timedelta(minutes=3)
    app.dependency_overrides[get_now] = lambda: later
    report = {
        "type": "end-of-call-report",
        "call": call(),
        "endedReason": "customer-ended-call",
        "cost": 0.2134,
        "durationSeconds": 182.4,
    }
    assert send(client, report).status_code == 200
    [record] = sessions(db)
    assert record.status == "ended" and record.end_reason == "caller_hung_up"
    assert record.ended_at == later
    assert record.usage == {"cost_usd": 0.2134, "duration_seconds": 182.4}
    # A later status update doesn't change it.
    send(client, {"type": "status-update", "status": "ended", "call": call()})
    [record] = sessions(db)
    assert record.end_reason == "caller_hung_up"


def test_the_website_shows_the_number_while_vapi_answers(client: TestClient, vapi) -> None:
    vapi["settings"] = vapi_settings(phone_number="+12605440490")
    assert client.get("/api/v1/business").json()["assistant_phone"] == "+12605440490"
    vapi["settings"] = vapi_settings(phone_number="+12605440490", vapi_enabled=False)
    assert client.get("/api/v1/business").json()["assistant_phone"] is None
