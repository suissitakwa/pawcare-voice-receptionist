"""Each phone call reaches the business whose number was dialed."""

import json

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, BusinessPhoneNumber, VoiceSession
from app.phone_api import get_call_starter, get_calls
from app.phone_numbers import (
    add_number,
    business_for_call,
    dialed_from_sip,
    dialed_from_vapi,
    normalize,
    remove_number,
)
from tests.test_phone import NOW, FakeCalls, incoming, phone_settings, post_event
from tests.test_vapi import send, vapi_settings

DEMO_LINE = "+18005550199"
SECOND_LINE = "+13105550142"


@pytest.fixture(autouse=True)
def no_numbers(db: Session):
    db.execute(delete(BusinessPhoneNumber))
    db.commit()
    yield
    db.rollback()
    db.execute(delete(BusinessPhoneNumber))
    db.commit()


@pytest.fixture
def phone() -> dict:
    """OpenAI calls with a fake call API, as in test_phone."""
    state = {"settings": phone_settings(), "calls": FakeCalls(), "started": []}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_calls] = lambda: state["calls"]
    app.dependency_overrides[get_call_starter] = lambda: lambda *args: state["started"].append(args)
    yield state
    for dependency in (get_settings, get_now, get_calls, get_call_starter):
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def vapi() -> dict:
    state = {"settings": vapi_settings()}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_now] = lambda: NOW
    yield state
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


def slug_of(db: Session, business_id) -> str:
    return db.scalar(select(Business.slug).where(Business.id == business_id))


def calls(db: Session) -> list[VoiceSession]:
    db.expire_all()
    return list(db.scalars(select(VoiceSession).where(VoiceSession.channel == "phone")))


def dialing(to: str, call_id: str = "rtc_abc123") -> dict:
    event = incoming(call_id)
    event["data"]["sip_headers"] = [
        header if header["name"] != "To" else {"name": "To", "value": f"<sip:{to}@sip.example>"}
        for header in event["data"]["sip_headers"]
    ]
    return event


def vapi_call(to: str | None, call_id: str = "call-1") -> dict:
    call = {"id": call_id, "customer": {"number": "+13235550100"}}
    message = {"type": "assistant-request", "call": call}
    if to is not None:
        message["phoneNumber"] = {"id": "pn_1", "number": to}
    return message


# Reading numbers


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+13105550142", "+13105550142"),
        ("(310) 555-0142", "+13105550142"),
        ("13105550142", "+13105550142"),
        ("+44 20 7946 0958", "+442079460958"),
        ("555-0142", None),
        ("not a number", None),
        (None, None),
    ],
)
def test_numbers_are_normalized_to_e164(raw, expected) -> None:
    assert normalize(raw) == expected


def test_dialed_number_comes_from_the_sip_to_header() -> None:
    assert dialed_from_sip(dialing(SECOND_LINE)["data"]["sip_headers"]) == SECOND_LINE
    assert dialed_from_sip([{"name": "From", "value": "<sip:+13235550100@x>"}]) is None
    assert dialed_from_sip("garbage") is None


def test_dialed_number_comes_from_vapis_phone_number() -> None:
    assert dialed_from_vapi(vapi_call(SECOND_LINE)) == SECOND_LINE
    nested = {"call": {"id": "c", "phoneNumber": {"number": SECOND_LINE}}}
    assert dialed_from_vapi(nested) == SECOND_LINE
    assert dialed_from_vapi(vapi_call(None)) is None


# Registering numbers


def test_a_number_can_be_registered_once(db, second_business) -> None:
    assert add_number(db, second_business, "(310) 555-0142") == SECOND_LINE
    assert add_number(db, second_business, SECOND_LINE) == SECOND_LINE
    with pytest.raises(ValueError, match="already belongs to second-studio"):
        add_number(db, "pawcare-demo", SECOND_LINE)
    db.commit()
    assert db.scalars(select(BusinessPhoneNumber.number)).all() == [SECOND_LINE]
    assert remove_number(db, SECOND_LINE) is True
    assert remove_number(db, SECOND_LINE) is False


@pytest.mark.parametrize("slug, number", [("pawcare-demo", "12345"), ("no-such", SECOND_LINE)])
def test_bad_registrations_are_refused(db, slug, number) -> None:
    with pytest.raises(ValueError):
        add_number(db, slug, number)


# Choosing the business


def test_without_registered_numbers_every_call_goes_to_the_deployment(db, second_business):
    settings = Settings()
    assert business_for_call(db, settings, SECOND_LINE).slug == "pawcare-demo"
    assert business_for_call(db, settings, None).slug == "pawcare-demo"


def test_registered_numbers_choose_their_business(db, second_business) -> None:
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    settings = Settings(phone_number=DEMO_LINE)
    assert business_for_call(db, settings, SECOND_LINE).slug == second_business
    assert business_for_call(db, settings, DEMO_LINE).slug == "pawcare-demo"
    assert business_for_call(db, settings, None).slug == "pawcare-demo"
    assert business_for_call(db, settings, "+12125550000") is None


# OpenAI calls


def test_openai_call_is_answered_for_the_dialed_business(client, db, phone, second_business):
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    assert post_event(client, dialing(SECOND_LINE)).json() == {"status": "answered"}
    [record] = calls(db)
    assert slug_of(db, record.business_id) == second_business
    [(_, body)] = phone["calls"].accepted
    assert "Second Studio" in body["instructions"]


def test_openai_call_to_an_unregistered_number_is_rejected(client, db, phone, second_business):
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    response = post_event(client, dialing("+12125550000"))
    assert response.json() == {"status": "declined"}
    assert phone["calls"].rejected == [("rtc_abc123", 603)]
    assert phone["calls"].accepted == []
    assert calls(db) == []


# Vapi calls


def test_vapi_call_is_answered_for_the_dialed_business(client, db, vapi, second_business):
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    assistant = send(client, vapi_call(SECOND_LINE)).json()["assistant"]
    assert "Second Studio" in assistant["firstMessage"]
    [record] = calls(db)
    assert slug_of(db, record.business_id) == second_business

    # Later messages for the call use that business's catalog: it offers Bath and Tidy only.
    def availability(service: str) -> dict:
        arguments = {"service": service, "size": "small", "date": "2027-01-05"}
        message = {
            "type": "tool-calls",
            "call": {"id": "call-1"},
            "toolCallList": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "check_availability", "arguments": arguments},
                }
            ],
        }
        [result] = send(client, message).json()["results"]
        return json.loads(result["result"])

    assert availability("full-groom")["error"] == "unknown_service"
    assert availability("bath-and-tidy")["ok"] is True


def test_vapi_call_to_an_unregistered_number_is_declined(client, db, vapi, second_business):
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    response = send(client, vapi_call("+12125550000")).json()
    assert "error" in response and "assistant" not in response
    assert calls(db) == []


# The number on each business's site


def test_each_site_shows_its_own_number(client, db, vapi, second_business) -> None:
    vapi["settings"] = Settings(vapi_enabled=True, vapi_secret="x" * 32, phone_number=DEMO_LINE)
    assert client.get("/api/v1/b/second-studio/business").json()["assistant_phone"] is None
    add_number(db, second_business, SECOND_LINE)
    db.commit()
    assert client.get("/api/v1/b/second-studio/business").json()["assistant_phone"] == SECOND_LINE
    assert client.get("/api/v1/b/pawcare-demo/business").json()["assistant_phone"] == DEMO_LINE
