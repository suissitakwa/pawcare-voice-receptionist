"""Home services: a plumber signs up, goes live, and the receptionist sends a technician only
to addresses in the area, with safety first, noting what needs doing and quoting only the
visit fee."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import website_details
from app.config import get_settings
from app.google_calendar import event_for
from app.models import Booking, Business, VoiceSession, WebhookEvent
from app.owner_email import compose
from app.provision import approve
from app.voice import build_instructions, message_instructions, phone_tools, tool_catalog
from app.voice_tools import prepare as voice_prepare
from tests.test_provision import no_leftover_businesses  # noqa: F401
from tests.test_subscribers import API, NOW, auth, complete, fixed_clock  # noqa: F401
from tests.test_website_details import MORE, more

PLUMBING = [
    {
        "name": "Leak repair visit",
        "description": "A plumber finds and fixes a leak.",
        "prices": [{"size": "any", "duration_minutes": 90, "price_cents": 8900}],
        "price_kind": "visit_fee",
    },
    {
        "name": "Drain cleaning",
        "prices": [{"size": "any", "duration_minutes": 60, "price_cents": 15000}],
    },
]


def plumber(client: TestClient, zips: list[str] | None) -> dict:
    headers = auth(client)
    complete(client, headers)
    details = {"services": PLUMBING, "team": [{"name": "Dana"}]}
    if zips is not None:
        details |= {"service_zip_codes": zips, "service_area": "San Jose and Campbell"}
    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={"business_type": "home_services", "details": details},
    )
    assert response.status_code == 200, response.text
    return headers


def test_a_plumber_must_say_where_they_travel_before_submitting(client) -> None:
    headers = plumber(client, zips=None)
    profile = client.get(f"{API}/profile", headers=headers).json()
    assert "service_zip_codes" in profile["missing"]
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 422


def test_a_plumber_goes_live_as_a_mobile_business(client, db) -> None:
    headers = plumber(client, zips=["95126", "95008"])
    # They never ticked "mobile": home services always travel.
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    db.expire_all()
    seed, gaps, _ = approve(db, "sam@example.com")
    db.commit()
    business = db.scalar(select(Business).where(Business.slug == seed.slug))
    assert business.industry == "home_services"
    assert business.is_mobile
    assert business.service_zip_codes == ["95008", "95126"]
    assert "service ZIP codes" not in gaps

    catalog = client.get(f"/api/v1/b/{seed.slug}/services").json()
    assert [s["slug"] for s in catalog["size_categories"]] == ["standard"]

    # The receptionist talks about a technician, puts safety first, and asks for the address.
    text = build_instructions(db, business, NOW.date(), channel="phone")
    assert "a home services business that sends a technician" in text
    assert "If the caller smells gas" in text
    assert text.index("smells gas") < text.index("Speak briefly")
    assert "Never diagnose a problem or quote the cost of a repair" in text
    assert "is mobile: they come to the customer" in text
    # (The shared test profile's answers still mention dogs; the wording itself doesn't.)
    assert "dog size" not in text and "the dog's" not in text and "stylist" not in text
    tools = {t["name"]: t for t in phone_tools(tool_catalog(db, business.id))}
    prepare_tool = tools["prepare_booking"]["parameters"]
    assert "service_address" in prepare_tool["required"]
    assert "pet_name" not in prepare_tool["properties"]

    message = message_instructions(db, business, None)
    assert "If the caller smells gas" in message
    assert "urgent problem such as a leak" in message

    # Bookings go only to addresses inside the area.
    day = "2027-01-05"  # a Tuesday, when the test profile is open
    slots = client.get(
        f"/api/v1/b/{seed.slug}/availability",
        params={"service": "leak-repair-visit", "date": day},
    ).json()["slots"]
    booking = {
        "service": "leak-repair-visit",
        "starts_at": slots[0]["starts_at"],
        "owner_name": "Jordan",
        "owner_phone": "(408) 555-0100",
    }
    outside = client.post(
        f"/api/v1/b/{seed.slug}/bookings/prepare",
        json={**booking, "service_address": "1 Main St, Austin, TX 78701"},
    )
    assert outside.status_code == 422
    assert outside.json()["error"]["code"] == "outside_service_area"
    inside = client.post(
        f"/api/v1/b/{seed.slug}/bookings/prepare",
        json={
            **booking,
            "service_address": "12 Elm St, San Jose, CA 95126",
            "job_details": "Kitchen sink leaking under the cabinet.",
        },
    )
    assert inside.status_code == 201, inside.text
    assert inside.json()["summary"]["service_address"] == "12 Elm St, San Jose, CA 95126"


def test_groomer_and_salon_wording_is_unchanged(db) -> None:
    demo = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    text = build_instructions(db, demo, NOW.date(), channel="phone")
    assert "smells gas" not in text
    assert "Never give veterinary or medical advice" in text


def test_the_website_can_say_a_plumber_comes_to_you() -> None:
    found, _ = website_details.check(more(), MORE, "home_services")
    assert found["mobile"] is not None
    assert found["service_area"] is not None


INSIDE = "12 Elm St, San Jose, CA 95126"
JOB = "Kitchen sink   leaking under the cabinet since this morning."


def live_plumber(client: TestClient, db) -> Business:
    headers = plumber(client, zips=["95126"])
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    db.expire_all()
    seed, _, _ = approve(db, "sam@example.com")
    db.commit()
    return db.scalar(select(Business).where(Business.slug == seed.slug))


def first_slot(client: TestClient, slug: str) -> str:
    return client.get(
        f"/api/v1/b/{slug}/availability",
        params={"service": "leak-repair-visit", "date": "2027-01-05"},
    ).json()["slots"][0]["starts_at"]


def test_a_plumber_booking_says_what_needs_doing(client, db) -> None:
    business = live_plumber(client, db)
    slug = business.slug
    booking = {
        "service": "leak-repair-visit",
        "starts_at": first_slot(client, slug),
        "owner_name": "Jordan",
        "owner_phone": "(408) 555-0100",
        "service_address": INSIDE,
    }
    missing = client.post(f"/api/v1/b/{slug}/bookings/prepare", json=booking)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "job_details_required"
    blank = client.post(f"/api/v1/b/{slug}/bookings/prepare", json={**booking, "job_details": " "})
    assert blank.json()["error"]["code"] == "job_details_required"
    too_long = client.post(
        f"/api/v1/b/{slug}/bookings/prepare", json={**booking, "job_details": "x" * 501}
    )
    assert too_long.status_code == 422

    prepared = client.post(
        f"/api/v1/b/{slug}/bookings/prepare", json={**booking, "job_details": JOB}
    )
    assert prepared.status_code == 201, prepared.text
    summary = prepared.json()["summary"]
    assert summary["job_details"] == "Kitchen sink leaking under the cabinet since this morning."
    assert summary["price_kind"] == "visit_fee"
    confirmed = client.post(
        f"/api/v1/b/{slug}/bookings/confirm",
        json={
            "confirmation_token": prepared.json()["confirmation_token"],
            "summary_hash": prepared.json()["summary_hash"],
        },
        headers={"Idempotency-Key": "plumber-key-0001"},
    )
    assert confirmed.status_code == 201, confirmed.text

    db.expire_all()
    saved = db.scalar(select(Booking).where(Booking.business_id == business.id))
    assert saved.job_details == summary["job_details"]
    event = db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.business_id == business.id,
            WebhookEvent.event_type == "booking.confirmed",
        )
    )
    data = event.payload["data"]
    assert data["job_details"] == summary["job_details"]
    assert data["price_kind"] == "visit_fee"

    # The owner, and the technician's calendar, see the job and that $89 is a visit fee.
    _, body = compose(event, business, get_settings())
    assert f"Job: {summary['job_details']}" in body
    assert "Price: $89.00 visit fee" in body
    description = event_for(saved, business)["description"]
    assert f"Job: {summary['job_details']}" in description
    assert "Price: $89.00 visit fee" in description

    # The public catalog says how each price is meant.
    services = client.get(f"/api/v1/b/{slug}/services").json()["services"]
    assert {s["slug"]: s["price_kind"] for s in services} == {
        "leak-repair-visit": "visit_fee",
        "drain-cleaning": "fixed",
    }


def test_the_receptionist_asks_what_needs_doing_and_quotes_only_the_visit_fee(client, db) -> None:
    business = live_plumber(client, db)
    text = build_instructions(db, business, NOW.date(), channel="phone")
    assert "90 minutes $89 visit fee" in text
    assert "60 minutes $150." in text
    assert "ask what needs doing" in text
    assert "never say or guess what a repair will cost" in text
    tools = {t["name"]: t for t in phone_tools(tool_catalog(db, business.id))}
    prepare_tool = tools["prepare_booking"]["parameters"]
    assert "job_details" in prepare_tool["properties"]
    assert "job_details" in prepare_tool["required"]

    record = VoiceSession(
        business_id=business.id,
        mode="test",
        channel="phone",
        client_hash="0" * 64,
        token_hash=uuid.uuid4().hex * 2,
    )
    db.add(record)
    db.flush()
    result = voice_prepare(
        db,
        business,
        record,
        {
            "service": "leak-repair-visit",
            "starts_at": first_slot(client, business.slug),
            "owner_name": "Jordan",
            "owner_phone": "(408) 555-0100",
            "service_address": INSIDE,
            "job_details": JOB,
            "details_read_back": True,
        },
        NOW,
    )
    db.rollback()
    assert result.ok
    assert result.result["job"].startswith("Kitchen sink leaking")
    assert result.result["price_dollars"] == 89
    assert "never say what the repair will cost" in result.result["price_note"]


def test_other_businesses_keep_no_job_details(client, db) -> None:
    from tests.test_booking_api import details

    prepared = client.post(
        "/api/v1/bookings/prepare", json=details(job_details="Please be gentle with her ears.")
    )
    assert prepared.status_code == 201, prepared.text
    assert prepared.json()["summary"]["job_details"] is None
    assert prepared.json()["summary"]["price_kind"] == "fixed"
    demo = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    text = build_instructions(db, demo, NOW.date(), channel="phone")
    assert "job_details" not in text and "visit fee" not in text
    tools = {t["name"]: t for t in phone_tools(tool_catalog(db, demo.id))}
    assert "job_details" not in tools["prepare_booking"]["parameters"]["properties"]
