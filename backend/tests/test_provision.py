"""A submitted subscriber profile becomes a live business after review."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Business, BusinessProfile
from app.provision import ProvisionError, create, draft, slugify
from app.schemas import BusinessSeed
from tests.test_subscribers import API, PASSWORD, auth, complete


@pytest.fixture(autouse=True)
def no_leftover_businesses(db: Session) -> Iterator[None]:
    def clean() -> None:
        db.rollback()
        db.execute(delete(Business).where(Business.slug.like("shaggy-chic%")))
        db.commit()

    clean()
    yield
    clean()


@pytest.fixture
def submitted(client: TestClient) -> dict:
    headers = auth(client)
    complete(client, headers)
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    return headers


def test_draft_fills_in_what_the_profile_knows(db, submitted) -> None:
    data = draft(db, "sam@example.com")
    assert data["slug"] == "shaggy-chic"
    assert data["name"] == "Shaggy Chic"
    assert data["contact_email"] == "sam@example.com"
    assert data["notify_email"] == "sam@example.com"
    assert data["contact_phone"] == "(415) 555-0134"
    assert data["is_demo"] is False
    assert data["faqs"] == [
        {"slug": "do-you-groom-cats", "question": "Do you groom cats?", "answer": "Dogs only."}
    ]
    # The rest is the template's example, to complete with the owner.
    assert data["services"] and data["groomers"]


def test_create_makes_the_business_live_for_its_owner(client, db, submitted) -> None:
    seed = BusinessSeed.model_validate(draft(db, "sam@example.com"))
    business_id, steps = create(db, "sam@example.com", seed)
    db.commit()

    profile = client.get(f"{API}/profile", headers=submitted).json()
    assert profile["business_slug"] == "shaggy-chic"
    assert db.scalar(select(BusinessProfile.business_id)) == business_id
    assert client.get("/api/v1/b/shaggy-chic/business").json()["name"] == "Shaggy Chic"
    # The sign-up password opens the new business's staff dashboard.
    login = client.post(
        "/api/v1/b/shaggy-chic/staff/login",
        json={"email": "sam@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    assert any("Google Calendar" in step for step in steps)

    with pytest.raises(ProvisionError, match="already live"):
        create(db, "sam@example.com", seed)


def test_a_taken_name_gets_a_free_address(db, submitted) -> None:
    seed = BusinessSeed.model_validate(draft(db, "sam@example.com"))
    db.add(
        Business(
            **seed.model_dump(
                include={
                    "name",
                    "tagline",
                    "about",
                    "timezone",
                    "contact_email",
                    "contact_phone",
                    "address",
                }
            ),
            slug="shaggy-chic",
        )
    )
    db.flush()
    assert draft(db, "sam@example.com")["slug"] == "shaggy-chic-2"


def test_only_submitted_pet_grooming_profiles_are_provisioned(client, db) -> None:
    headers = auth(client)
    with pytest.raises(ProvisionError, match="hasn't submitted"):
        draft(db, "sam@example.com")
    complete(client, headers)
    client.patch(f"{API}/profile", headers=headers, json={"business_type": "hair_salon"})
    client.post(f"{API}/profile/submit", headers=headers)
    db.expire_all()
    with pytest.raises(ProvisionError, match="only handles pet grooming"):
        draft(db, "sam@example.com")
    with pytest.raises(ProvisionError, match="No subscriber"):
        draft(db, "nobody@example.com")


def test_a_file_with_problems_creates_nothing(db, submitted) -> None:
    data = draft(db, "sam@example.com")
    data["groomers"] = []
    with pytest.raises(ValueError, match="no groomer"):
        create(db, "sam@example.com", BusinessSeed.model_validate(data))
    db.rollback()
    assert db.scalar(select(BusinessProfile.business_id)) is None
    assert db.scalar(select(Business.id).where(Business.slug == "shaggy-chic")) is None


def test_slugs_are_web_safe() -> None:
    assert slugify("Paws & Claws — Austin!") == "paws-claws-austin"
    assert slugify("!!!") == "business"
