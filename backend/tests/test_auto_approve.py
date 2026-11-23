"""With AUTO_APPROVE_SIGNUPS on, sign-ups go live at once, rung by their sign-up number."""

from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import go_live, provision
from app.config import Settings, get_settings
from app.main import app
from app.models import Business, BusinessPhoneNumber, WebhookEvent
from app.owner_email import message_for
from app.phone_numbers import add_number, business_for_call
from app.provision import ProvisionError
from tests.test_subscribers import API, COMPLETE_DETAILS, auth, fixed_clock  # noqa: F401

DEMO_LINE = "+12696992551"


@pytest.fixture(autouse=True)
def clean(db: Session) -> Iterator[None]:
    def remove() -> None:
        db.rollback()
        db.execute(delete(BusinessPhoneNumber))
        db.execute(delete(Business).where(Business.slug.like("shaggy-chic%")))
        db.commit()

    remove()
    app.dependency_overrides[get_settings] = lambda: Settings(auto_approve_signups=True)
    yield
    remove()


def submit(client, email: str, phone: str = "(269) 699-2551") -> dict:
    headers = auth(client, email=email)
    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={
            "business_name": "Shaggy Chic",
            "business_type": "pet_grooming",
            "details": {**COMPLETE_DETAILS, "business_phone": phone},
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(f"{API}/profile/submit", headers=headers)
    assert response.status_code == 200
    return response.json()


def holder(db: Session, number: str = DEMO_LINE) -> str | None:
    return db.scalar(
        select(Business.slug)
        .join(BusinessPhoneNumber, BusinessPhoneNumber.business_id == Business.id)
        .where(BusinessPhoneNumber.number == number)
    )


def events(db: Session, event_type: str) -> list[WebhookEvent]:
    return db.scalars(select(WebhookEvent).where(WebhookEvent.event_type == event_type)).all()


def test_a_sign_up_goes_live_with_its_own_number(client, db) -> None:
    profile = submit(client, "a@example.com")
    assert profile["business_slug"] == "shaggy-chic"
    assert holder(db) == "shaggy-chic"
    assert business_for_call(db, Settings(), DEMO_LINE).slug == "shaggy-chic"
    # The owner hears both: set up, then the number.
    assert [e.payload["data"]["owner_email"] for e in events(db, go_live.LIVE)] == ["a@example.com"]
    assert [e.payload["data"]["number"] for e in events(db, go_live.PHONE_READY)] == [DEMO_LINE]
    # You're told it went live on its own.
    [notice] = events(db, "profile.submitted")
    body = message_for(notice, None, Settings(), "team@example.com").get_content()
    assert "approved automatically" in body
    assert "provision approve" not in body


def test_the_newest_sign_up_with_the_demo_line_takes_it(client, db) -> None:
    add_number(db, "pawcare-demo", DEMO_LINE)
    db.commit()
    submit(client, "a@example.com")
    assert holder(db) == "shaggy-chic"
    second = submit(client, "b@example.com")
    assert second["business_slug"] == "shaggy-chic-2"
    assert holder(db) == "shaggy-chic-2"
    # The earlier one keeps its booking site and dashboard, just not the number.
    assert db.scalar(select(Business.id).where(Business.slug == "shaggy-chic")) is not None
    assert len(events(db, go_live.PHONE_READY)) == 2


def test_a_number_you_registered_for_a_business_is_never_taken(client, db, second_business):
    add_number(db, second_business, DEMO_LINE)
    db.commit()
    profile = submit(client, "a@example.com")
    # Approved, but the number stays with the business you gave it to.
    assert profile["business_slug"] == "shaggy-chic"
    assert holder(db) == second_business
    assert events(db, go_live.PHONE_READY) == []


def test_a_number_without_an_area_code_is_skipped(client, db) -> None:
    profile = submit(client, "a@example.com", phone="555-0134")
    assert profile["business_slug"] == "shaggy-chic"
    assert db.scalar(select(BusinessPhoneNumber.id)) is None


def test_a_profile_that_cannot_be_approved_stays_waiting(client, db, monkeypatch) -> None:
    def refuse(session, email):
        raise ProvisionError("The profile has no hours.")

    monkeypatch.setattr(provision, "approve", refuse)
    profile = submit(client, "a@example.com")
    assert profile["status"] == "submitted"
    assert profile["business_slug"] is None
    assert db.scalar(select(BusinessPhoneNumber.id)) is None


def test_switched_off_every_sign_up_waits(client, db) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(auto_approve_signups=False)
    profile = submit(client, "a@example.com")
    assert profile["business_slug"] is None
    assert holder(db) is None
    [notice] = events(db, "profile.submitted")
    assert "provision approve" in message_for(notice, None, Settings(), "t@x").get_content()
