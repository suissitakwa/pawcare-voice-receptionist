"""Home services: a plumber signs up, goes live, and the receptionist sends a technician only
to addresses in the area, with safety first."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import website_details
from app.models import Business
from app.provision import approve
from app.voice import build_instructions, message_instructions, phone_tools, tool_catalog
from tests.test_provision import no_leftover_businesses  # noqa: F401
from tests.test_subscribers import API, NOW, auth, complete, fixed_clock  # noqa: F401
from tests.test_website_details import MORE, more

PLUMBING = [
    {
        "name": "Leak repair visit",
        "description": "A plumber finds and fixes a leak.",
        "prices": [{"size": "any", "duration_minutes": 90, "price_cents": 8900}],
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
        json={**booking, "service_address": "12 Elm St, San Jose, CA 95126"},
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
