"""Mobile groomers: the customer's address is needed, and must be inside the service area."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.businesses import TEMPLATE_FILE
from app.google_calendar import event_for
from app.models import Booking, Business, WebhookEvent
from app.schemas import BusinessSeed
from app.voice import build_instructions, phone_tools, tool_catalog
from tests.test_booking_api import NOW, confirm, details, fixed_clock, prepare  # noqa: F401

INSIDE = "12 Elm St, San Jose, CA 95126"


def make_mobile(db: Session, zips=("95126", "95128"), area="San Jose and Campbell") -> Business:
    db.execute(
        update(Business)
        .where(Business.slug == "pawcare-demo")
        .values(is_mobile=True, service_zip_codes=list(zips), service_area=area)
    )
    db.commit()
    return db.scalar(select(Business).where(Business.slug == "pawcare-demo"))


def code(response) -> str:
    return response.json()["error"]["code"]


def test_a_mobile_booking_needs_an_address_in_the_area(client: TestClient, db: Session) -> None:
    make_mobile(db)
    assert code(prepare(client)) == "address_required"
    assert code(prepare(client, service_address="12 Elm St, San Jose")) == "zip_required"
    outside = prepare(client, service_address="1 Main St, Palo Alto 94301")
    assert code(outside) == "outside_service_area"
    assert "They cover San Jose and Campbell" in outside.json()["error"]["message"]

    prepared = prepare(client, service_address=f"  {INSIDE} ")
    assert prepared.status_code == 201
    assert prepared.json()["summary"]["service_address"] == INSIDE
    assert confirm(client, prepared.json()).status_code == 201
    db.expire_all()
    booking = db.scalar(select(Booking))
    assert booking.service_address == INSIDE
    # It reaches the groomer's calendar as the event's location, and the owner email.
    business = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    assert event_for(booking, business)["location"] == INSIDE
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.event_type == "booking.confirmed"))
    assert event.payload["data"]["service_address"] == INSIDE


def test_without_zip_codes_any_address_will_do(client: TestClient, db: Session) -> None:
    make_mobile(db, zips=())
    assert prepare(client, service_address="5 Oak Ave, Anywhere").status_code == 201


def test_a_salon_based_business_keeps_no_address(client: TestClient, db: Session) -> None:
    prepared = prepare(client, service_address=INSIDE)
    assert prepared.status_code == 201
    assert prepared.json()["summary"]["service_address"] is None


def test_the_receptionist_asks_mobile_callers_where_to_come(db: Session) -> None:
    demo = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    assert "is mobile" not in build_instructions(db, demo, NOW.date(), channel="phone")
    plain = {t["name"]: t for t in phone_tools(tool_catalog(db, demo.id))}
    assert "service_address" not in plain["prepare_booking"]["parameters"]["properties"]

    mobile = make_mobile(db)
    text = build_instructions(db, mobile, NOW.date(), channel="phone")
    assert "is mobile: they come to the customer" in text
    assert "They cover San Jose and Campbell." in text
    tools = {t["name"]: t for t in phone_tools(tool_catalog(db, mobile.id))}
    prepare_tool = tools["prepare_booking"]["parameters"]
    assert "service_address" in prepare_tool["properties"]
    assert "service_address" in prepare_tool["required"]


def test_only_groomers_and_home_services_can_be_mobile() -> None:
    data = json.loads(TEMPLATE_FILE.read_text())
    BusinessSeed.model_validate({**data, "is_mobile": True, "service_zip_codes": ["95126"]})
    BusinessSeed.model_validate({**data, "industry": "home_services", "is_mobile": True})
    with pytest.raises(ValidationError, match="only a pet grooming or home services business"):
        BusinessSeed.model_validate({**data, "industry": "barbershop", "is_mobile": True})
    with pytest.raises(ValidationError, match="a home services business must be mobile"):
        BusinessSeed.model_validate({**data, "industry": "home_services"})
    with pytest.raises(ValidationError, match="five-digit ZIP"):
        BusinessSeed.model_validate({**data, "is_mobile": True, "service_zip_codes": ["9512"]})
