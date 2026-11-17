"""Public routes serve the business named in the address, and never another business's data."""

from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Booking, Business, VoiceSession

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
DEMO = "/api/v1/b/pawcare-demo"
SECOND = "/api/v1/b/second-studio"


@pytest.fixture(autouse=True)
def fixed_clock_and_settings() -> Iterator[None]:
    settings = Settings(voice_mode="test")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


def prepare(client: TestClient, base: str):
    return client.post(
        f"{base}/bookings/prepare",
        json={
            "service": "bath-and-tidy",
            "size": "small",
            "starts_at": NINE.isoformat(),
            "owner_name": "Jordan Example",
            "owner_email": "jordan@example.com",
            "pet_name": "Biscuit",
        },
    )


def confirm(client: TestClient, base: str, prepared: dict, key: str = "key-0001-abcdef"):
    return client.post(
        f"{base}/bookings/confirm",
        json={
            "confirmation_token": prepared["confirmation_token"],
            "summary_hash": prepared["summary_hash"],
        },
        headers={"Idempotency-Key": key},
    )


def bookings(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Booking))


# Choosing the business


def test_unprefixed_routes_still_serve_the_deployments_business(client, second_business) -> None:
    assert client.get("/api/v1/business").json() == client.get(f"{DEMO}/business").json()


def test_each_business_is_served_from_its_own_rows(client, second_business) -> None:
    assert client.get(f"{SECOND}/business").json()["name"] == "Second Studio"
    assert client.get(f"{DEMO}/business").json()["name"] == "PawCare Grooming Studio"

    second_services = [s["slug"] for s in client.get(f"{SECOND}/services").json()["services"]]
    demo_services = [s["slug"] for s in client.get(f"{DEMO}/services").json()["services"]]
    assert second_services == ["bath-and-tidy"]
    assert len(demo_services) > 1

    second_faqs = [faq["question"] for faq in client.get(f"{SECOND}/faqs").json()]
    assert second_faqs == ["Is there parking?"]
    assert "Is there parking?" not in [faq["question"] for faq in client.get(f"{DEMO}/faqs").json()]


def test_unknown_business_is_404(client) -> None:
    response = client.get("/api/v1/b/no-such-business/services")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


@pytest.mark.parametrize("slug", ["Upper", "under_score", "-leading", "x" * 65])
def test_malformed_slug_finds_no_business(client, slug) -> None:
    response = client.get(f"/api/v1/b/{slug}/business")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


def test_phone_number_shows_only_on_the_deployments_business(client, second_business) -> None:
    settings = Settings(phone_number="+13235550100", vapi_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    assert client.get(f"{DEMO}/business").json()["assistant_phone"] == "+13235550100"
    assert client.get(f"{SECOND}/business").json()["assistant_phone"] is None


# Bookings stay with their business


def test_booking_at_one_business_works_through_its_address(client, db, second_business) -> None:
    prepared = prepare(client, SECOND)
    assert prepared.status_code == 201
    assert confirm(client, SECOND, prepared.json()).status_code == 201
    assert bookings(db) == 1


def test_summary_prepared_at_one_business_cannot_be_confirmed_at_another(
    client, db, second_business
) -> None:
    prepared = prepare(client, DEMO).json()
    response = confirm(client, SECOND, prepared)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "confirmation_not_found"
    assert bookings(db) == 0


def test_booking_at_one_business_leaves_the_others_slot_open(client, second_business) -> None:
    assert confirm(client, DEMO, prepare(client, DEMO).json()).status_code == 201

    def open_starts(base: str) -> list[str]:
        params = {"service": "bath-and-tidy", "size": "small", "date": "2027-01-05"}
        body = client.get(f"{base}/availability", params=params).json()
        return [slot["local_start"] for slot in body["slots"]]

    nine = "2027-01-05T09:00:00-08:00"
    assert nine not in open_starts(DEMO)
    assert nine in open_starts(SECOND)


# Voice sessions stay with their business


def test_voice_session_belongs_to_the_business_it_started_at(client, db, second_business) -> None:
    started = client.post(f"{SECOND}/voice/sessions")
    assert started.status_code == 201
    db.expire_all()
    record = db.get(VoiceSession, started.json()["session_id"])
    assert record.business_id == db.scalar(
        select(Business.id).where(Business.slug == second_business)
    )


def test_voice_session_cannot_be_used_through_another_business(client, second_business) -> None:
    voice = client.post(f"{DEMO}/voice/sessions").json()
    body = {"session_token": voice["session_token"], "arguments": "{}"}

    tool = client.post(
        f"{SECOND}/voice/sessions/{voice['session_id']}/tools/check_availability", json=body
    )
    assert tool.status_code == 404
    assert tool.json()["error"]["code"] == "voice_session_not_found"

    end = client.post(
        f"{SECOND}/voice/sessions/{voice['session_id']}/end",
        json={"session_token": voice["session_token"], "reason": "user_ended"},
    )
    assert end.status_code == 404
    assert end.json()["error"]["code"] == "voice_session_not_found"
