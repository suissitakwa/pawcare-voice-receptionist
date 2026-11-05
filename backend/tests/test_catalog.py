import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import Business, BusinessHours, Faq, Service, ServiceSizeRule
from app.schemas import BusinessSeed
from app.seed import SEED_FILE, seed_business


def seed_dict() -> dict:
    return json.loads(SEED_FILE.read_text())


def test_demo_data_has_three_services(client: TestClient) -> None:
    services = client.get("/api/v1/services").json()["services"]
    assert [service["slug"] for service in services] == [
        "bath-and-tidy",
        "full-groom",
        "nail-and-paw",
    ]


def test_duration_and_price_depend_on_size(client: TestClient) -> None:
    services = client.get("/api/v1/services").json()["services"]
    options = {o["size"]: o for o in services[1]["options"]}
    assert options["small"] == {"size": "small", "duration_minutes": 90, "price_cents": 7000}
    assert options["large"] == {"size": "large", "duration_minutes": 150, "price_cents": 11500}


def test_staff_assessment_size_has_no_bookable_option(client: TestClient) -> None:
    body = client.get("/api/v1/services").json()
    flagged = [s["slug"] for s in body["size_categories"] if s["requires_staff_assessment"]]
    assert flagged == ["extra-large"]
    for service in body["services"]:
        assert "extra-large" not in {option["size"] for option in service["options"]}


def test_seed_rejects_a_price_for_a_staff_assessment_size() -> None:
    data = seed_dict()
    data["services"][0]["options"].append(
        {"size": "extra-large", "duration_minutes": 60, "price_cents": 100}
    )
    with pytest.raises(ValidationError, match="needs staff assessment"):
        BusinessSeed.model_validate(data)


def test_seed_rejects_an_unknown_size() -> None:
    data = seed_dict()
    data["services"][0]["options"][0]["size"] = "tiny"
    with pytest.raises(ValidationError, match="unknown size"):
        BusinessSeed.model_validate(data)


def test_inactive_service_is_hidden(client: TestClient, db: Session) -> None:
    db.execute(update(Service).where(Service.slug == "full-groom").values(is_active=False))
    db.commit()
    slugs = [s["slug"] for s in client.get("/api/v1/services").json()["services"]]
    assert "full-groom" not in slugs


def test_price_comes_from_the_database(client: TestClient, db: Session) -> None:
    db.execute(update(ServiceSizeRule).values(price_cents=1234))
    db.commit()
    services = client.get("/api/v1/services").json()["services"]
    assert services[0]["options"][0]["price_cents"] == 1234


def test_business_returns_ordered_hours_and_closed_days_are_absent(client: TestClient) -> None:
    hours = client.get("/api/v1/business").json()["hours"]
    assert [h["weekday"] for h in hours] == [1, 2, 3, 4, 5]
    assert hours[0]["opens_at"] == "09:00:00"
    assert hours[0]["closes_at"] == "17:00:00"


def test_database_rejects_inverted_hours(db: Session) -> None:
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    db.add(BusinessHours(business_id=business_id, weekday=0, opens_at="17:00", closes_at="09:00"))
    with pytest.raises(IntegrityError, match="closes_after_opens"):
        db.commit()
    db.rollback()


def test_database_rejects_zero_duration(db: Session) -> None:
    db.execute(update(ServiceSizeRule).values(duration_minutes=60))
    db.commit()
    with pytest.raises(IntegrityError, match="duration_positive"):
        db.execute(update(ServiceSizeRule).values(duration_minutes=0))
    db.rollback()


def test_faqs_are_ordered_and_expose_only_public_fields(client: TestClient) -> None:
    faqs = client.get("/api/v1/faqs").json()
    assert faqs[0]["slug"] == "what-dogs"
    assert set(faqs[0]) == {"slug", "question", "answer"}


def test_faqs_never_offer_veterinary_advice(client: TestClient) -> None:
    by_slug = {faq["slug"]: faq for faq in client.get("/api/v1/faqs").json()}
    assert "can't give veterinary advice" in by_slug["health-concerns"]["answer"]


def test_reseeding_removes_stale_rows_and_keeps_service_ids(db: Session) -> None:
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    db.add(Faq(business_id=business_id, slug="stale", question="q", answer="a", display_order=99))
    db.commit()
    before = db.scalar(select(Service.id).where(Service.slug == "full-groom"))

    seed_business(db)
    db.commit()

    assert db.scalar(select(Service.id).where(Service.slug == "full-groom")) == before
    assert db.scalar(select(func.count()).select_from(Faq).where(Faq.slug == "stale")) == 0


def test_unknown_business_gets_a_structured_404_on_every_catalog_route(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(business_slug="other-business")
    for path in ("/api/v1/business", "/api/v1/services", "/api/v1/faqs"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "business_not_configured"


def test_catalog_returns_503_when_database_is_down(client: TestClient, database_down: None) -> None:
    for path in ("/api/v1/services", "/api/v1/faqs"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
