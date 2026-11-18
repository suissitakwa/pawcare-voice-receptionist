"""A business with several staff: anyone free or a requested person, who does which service,
blocks for one person or everyone, and changes that stay with the same person."""

import json
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.availability import staff_for
from app.businesses import TEMPLATE_FILE, problems
from app.main import app, get_now
from app.models import Booking, Business, Groomer, Service, StaffUser
from app.schemas import BusinessSeed
from app.seed import load_seed, seed_business
from app.voice import build_instructions, phone_tools, tool_catalog

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
SLUG = "two-chairs"
API = f"/api/v1/b/{SLUG}"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def salon(db: Session) -> str:
    """Alex does everything; Bo only does Bath and Tidy. Same hours as the demo."""
    seed = load_seed().model_copy(update={"slug": SLUG, "name": "Two Chairs", "is_demo": False})
    first = seed.groomers[0]
    seed.groomers = [
        first.model_copy(update={"slug": "alex", "display_name": "Alex"}),
        first.model_copy(
            update={"slug": "bo", "display_name": "Bo", "services": ["bath-and-tidy"]}
        ),
    ]
    seed_business(db, seed)
    db.commit()
    return SLUG


def details(**overrides) -> dict:
    body = {
        "service": "bath-and-tidy",
        "size": "small",
        "starts_at": NINE.isoformat(),
        "owner_name": "Jordan Example",
        "owner_email": "jordan@example.com",
        "pet_name": "Biscuit",
    }
    body.update(overrides)
    return body


def book(client: TestClient, key: str, **overrides):
    prepared = client.post(f"{API}/bookings/prepare", json=details(**overrides))
    if prepared.status_code != 201:
        return prepared
    body = prepared.json()
    return client.post(
        f"{API}/bookings/confirm",
        json={
            "confirmation_token": body["confirmation_token"],
            "summary_hash": body["summary_hash"],
        },
        headers={"Idempotency-Key": key},
    )


def starts(client: TestClient, **params) -> list[str]:
    query = {"service": "bath-and-tidy", "size": "small", "date": "2027-01-05", **params}
    response = client.get(f"{API}/availability", params=query)
    assert response.status_code == 200, response.text
    return [slot["local_start"][11:16] for slot in response.json()["slots"]]


def staff_of(db: Session, reference: str) -> str:
    db.expire_all()
    return db.scalar(
        select(Groomer.slug)
        .join(Booking, Booking.groomer_id == Groomer.id)
        .where(Booking.reference == reference)
    )


# Booking


def test_two_staff_can_take_the_same_time(client, db, salon) -> None:
    first = book(client, "key-first-00001")
    second = book(client, "key-second-0001")
    assert first.status_code == second.status_code == 201
    assert {staff_of(db, first.json()["reference"]), staff_of(db, second.json()["reference"])} == {
        "alex",
        "bo",
    }
    third = book(client, "key-third-00001")
    assert third.json()["error"]["code"] == "slot_unavailable"


def test_a_time_stays_open_while_anyone_is_free(client, salon) -> None:
    assert "09:00" in starts(client)
    book(client, "key-first-00001", staff="alex")
    assert "09:00" in starts(client)
    assert "09:00" not in starts(client, staff="alex")
    assert "09:00" in starts(client, staff="bo")


def test_no_preference_goes_to_whoever_has_fewer_bookings(client, db, salon) -> None:
    book(client, "key-first-00001", staff="alex", starts_at=NINE.replace(hour=14).isoformat())
    booked = book(client, "key-second-0001")
    assert staff_of(db, booked.json()["reference"]) == "bo"


def test_a_requested_person_is_named_in_the_summary(client, salon) -> None:
    prepared = client.post(f"{API}/bookings/prepare", json=details(staff="bo"))
    assert prepared.json()["summary"]["staff"] == "Bo"


def test_only_qualified_staff_are_offered_a_service(client, db, salon) -> None:
    full = {"service": "full-groom"}
    book(client, "key-first-00001", service="full-groom")
    # Bo doesn't do full grooms, so with Alex booked at nine nobody can take one then.
    assert "09:00" not in starts(client, **full)
    refused = client.post(f"{API}/bookings/prepare", json=details(service="full-groom", staff="bo"))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "staff_not_qualified"


def test_unknown_staff_is_refused(client, salon) -> None:
    response = client.get(
        f"{API}/availability",
        params={"service": "bath-and-tidy", "size": "small", "date": "2027-01-05", "staff": "zed"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_staff"


def test_one_staff_business_has_no_staff_in_its_summary(client) -> None:
    prepared = client.post(
        "/api/v1/bookings/prepare", json=details(service="full-groom", size="medium")
    )
    assert prepared.json()["summary"]["staff"] is None


def test_catalog_lists_staff_only_when_there_is_a_choice(client, salon) -> None:
    everything = ["bath-and-tidy", "full-groom", "nail-and-paw"]
    staff = client.get(f"{API}/services").json()["staff"]
    assert staff == [
        {"slug": "alex", "name": "Alex", "services": everything},
        {"slug": "bo", "name": "Bo", "services": ["bath-and-tidy"]},
    ]
    assert client.get("/api/v1/services").json()["staff"] == []


# Staff dashboard


@pytest.fixture
def owner_headers(client, db, salon) -> dict:
    business_id = db.scalar(select(Business.id).where(Business.slug == SLUG))
    db.add(
        StaffUser(
            business_id=business_id,
            email="owner@twochairs.example",
            display_name="Owner",
            password_hash=PasswordHasher().hash(PASSWORD),
        )
    )
    db.commit()
    token = client.post(
        f"{API}/staff/login", json={"email": "owner@twochairs.example", "password": PASSWORD}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def block(client, headers, **extra):
    body = {
        "start_date": "2027-01-05",
        "start_time": "09:00",
        "end_date": "2027-01-05",
        "end_time": "10:00",
        **extra,
    }
    return client.post("/api/v1/staff/blocks", json=body, headers=headers)


def test_blocking_one_person_leaves_the_other_bookable(client, owner_headers) -> None:
    created = block(client, owner_headers, staff="alex")
    assert created.status_code == 201
    assert created.json()["block"]["staff"] == "Alex"
    assert "09:00" not in starts(client, staff="alex")
    assert "09:00" in starts(client)


def test_blocking_without_a_person_closes_the_business(client, owner_headers) -> None:
    book(client, "key-first-00001", staff="bo")
    created = block(client, owner_headers)
    assert created.json()["block"]["staff"] is None
    assert [c["staff"] for c in created.json()["conflicts"]] == ["Bo"]
    assert "09:00" not in starts(client)
    listed = client.get("/api/v1/staff/blocks", headers=owner_headers).json()
    assert [b["staff"] for b in listed] == [None]


def test_dashboard_shows_who_each_booking_is_with(client, owner_headers) -> None:
    book(client, "key-first-00001", staff="bo")
    rows = client.get("/api/v1/staff/bookings", headers=owner_headers).json()
    assert [row["staff"] for row in rows] == ["Bo"]
    team = client.get("/api/v1/staff/team", headers=owner_headers).json()
    assert team == [{"slug": "alex", "name": "Alex"}, {"slug": "bo", "name": "Bo"}]


# Voice


def test_phone_tools_offer_staff_only_when_there_is_a_choice(db, salon) -> None:
    business_id = db.scalar(select(Business.id).where(Business.slug == SLUG))
    tools = {t["name"]: t for t in phone_tools(tool_catalog(db, business_id))}
    assert tools["check_availability"]["parameters"]["properties"]["staff"]["enum"] == [
        "alex",
        "bo",
    ]
    assert "staff" in tools["prepare_booking"]["parameters"]["properties"]
    demo_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    demo = {t["name"]: t for t in phone_tools(tool_catalog(db, demo_id))}
    assert "staff" not in demo["check_availability"]["parameters"]["properties"]


def test_instructions_say_who_does_what(db, salon) -> None:
    business = db.scalar(select(Business).where(Business.slug == SLUG))
    text = build_instructions(db, business, NOW.date(), channel="phone")
    assert "- Alex (staff: alex): every service." in text
    assert "- Bo (staff: bo): Bath and Tidy." in text


def test_staff_for_respects_requests(db, salon) -> None:
    business_id = db.scalar(select(Business.id).where(Business.slug == SLUG))
    service_id = db.scalar(
        select(Service.id).where(Service.business_id == business_id, Service.slug == "full-groom")
    )
    assert [m.slug for m in staff_for(db, business_id, service_id)] == ["alex"]


# Business files


def test_reloading_without_a_person_stops_their_bookings(client, db, salon) -> None:
    seed = load_seed().model_copy(update={"slug": SLUG, "name": "Two Chairs", "is_demo": False})
    seed.groomers = [seed.groomers[0].model_copy(update={"slug": "alex", "display_name": "Alex"})]
    seed_business(db, seed)
    db.commit()
    assert client.get(f"{API}/services").json()["staff"] == []
    assert book(client, "key-first-00001", staff="bo").json()["error"]["code"] == "unknown_staff"


def test_business_file_checks_staff_services() -> None:
    data = json.loads(TEMPLATE_FILE.read_text())
    groomer = data["groomers"][0]
    data["groomers"] = [{**groomer, "services": ["nail-trim", "haircut"]}]
    found = problems(BusinessSeed.model_validate(data))
    assert "main-groomer: service 'haircut' is not in services" in found
    assert "nobody does bath-and-brush, so it could never be booked" in found
