"""Creating and updating a real business from a file."""

import json
from collections.abc import Iterator
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.businesses import TEMPLATE_FILE, load_business, problems, read_seed
from app.main import app, get_now
from app.models import Business, Service, StaffUser
from app.schemas import BusinessSeed

SLUG = "happy-tails-austin"
CHICAGO = ZoneInfo("America/Chicago")


def template() -> BusinessSeed:
    return BusinessSeed.model_validate(json.loads(TEMPLATE_FILE.read_text()))


@pytest.fixture
def clean(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.execute(delete(Business).where(Business.slug == SLUG))
    db.commit()


def test_the_template_is_a_valid_business() -> None:
    assert problems(template()) == []


def test_a_loaded_business_can_be_booked_at_its_own_address(
    client: TestClient, db: Session, clean
) -> None:
    load_business(db, template())
    db.commit()
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 4, 8, 0, tzinfo=CHICAGO)
    try:
        profile = client.get(f"/api/v1/b/{SLUG}/business").json()
        assert profile["name"] == "Happy Tails Grooming"
        assert profile["is_demo"] is False
        services = client.get(f"/api/v1/b/{SLUG}/services").json()["services"]
        assert [service["slug"] for service in services] == [
            "bath-and-brush",
            "full-groom",
            "nail-trim",
        ]
        params = {"service": "nail-trim", "size": "small", "date": "2027-01-05"}
        slots = client.get(f"/api/v1/b/{SLUG}/availability", params=params).json()["slots"]
        assert slots[0]["local_start"] == "2027-01-05T09:00:00-06:00"
    finally:
        app.dependency_overrides.pop(get_now, None)


def test_loading_never_overwrites_without_update(db: Session, clean) -> None:
    load_business(db, template())
    db.commit()
    with pytest.raises(ValueError, match="already exists"):
        load_business(db, template())
    with pytest.raises(ValueError, match="No business"):
        load_business(db, template().model_copy(update={"slug": "not-there-yet"}), update=True)


def test_update_replaces_details_and_switches_off_dropped_services(db: Session, clean) -> None:
    business_id = load_business(db, template())
    db.add(
        StaffUser(
            business_id=business_id,
            email="owner@happytails.example",
            display_name="Owner",
            password_hash=PasswordHasher().hash("correct-horse-battery"),
        )
    )
    db.commit()

    changed = template()
    changed.name = "Happy Tails Dog Spa"
    changed.services = [service for service in changed.services if service.slug != "nail-trim"]
    assert load_business(db, changed, update=True) == business_id
    db.commit()
    db.expire_all()

    assert db.get(Business, business_id).name == "Happy Tails Dog Spa"
    nail_trim = db.scalar(
        select(Service).where(Service.business_id == business_id, Service.slug == "nail-trim")
    )
    assert nail_trim is not None and nail_trim.is_active is False
    # Staff, like bookings, numbers and integrations, are never touched by a reload.
    assert db.scalar(select(StaffUser.email).where(StaffUser.business_id == business_id))


def broken(**changes) -> BusinessSeed:
    seed = template()
    for field, value in changes.items():
        setattr(seed, field, value)
    return seed


@pytest.mark.parametrize(
    "seed, message",
    [
        (broken(slug="Happy Tails"), "lowercase letters"),
        (broken(contact_email="not-an-email"), "not an email"),
        (broken(groomers=[]), "no groomer"),
        (broken(services=[]), "no services"),
        (broken(hours=[]), "no opening hours"),
        (broken(services=template().services[:1] * 2), "is used 2 times"),
        (broken(hours=template().hours[:1] * 2), "one per day"),
    ],
)
def test_problems_are_explained(seed: BusinessSeed, message: str) -> None:
    assert any(message in problem for problem in problems(seed))


def test_closing_before_opening_is_a_problem() -> None:
    seed = template()
    seed.hours[0].closes_at = time(8, 0)
    assert any("closes before it opens" in problem for problem in problems(seed))


def test_a_broken_file_is_refused_with_reasons(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{ not json")
    with pytest.raises(ValueError, match="Can't read"):
        read_seed(bad_json)

    data = json.loads(TEMPLATE_FILE.read_text())
    data["timezone"] = "Mars/Olympus"
    data["services"][0]["options"][0]["size"] = "huge"
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(data))
    with pytest.raises(ValueError) as error:
        read_seed(wrong)
    assert "timezone" in str(error.value)


def test_loading_refuses_a_file_with_problems(db: Session) -> None:
    with pytest.raises(ValueError, match="no groomer"):
        load_business(db, broken(groomers=[]))
    assert db.scalar(select(Business.id).where(Business.slug == SLUG)) is None
