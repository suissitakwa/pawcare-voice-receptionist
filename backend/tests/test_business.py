from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import Business
from app.seed import seed_business


def test_returns_seeded_business_from_database(client: TestClient) -> None:
    response = client.get("/api/v1/business")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "PawCare Grooming Studio"
    assert body["timezone"] == "America/Los_Angeles"
    assert body["is_demo"] is True


def test_response_reflects_database_not_hardcoded_values(client: TestClient, db: Session) -> None:
    business = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    business.name = "Renamed By Staff"
    db.commit()
    assert client.get("/api/v1/business").json()["name"] == "Renamed By Staff"


def test_internal_fields_are_not_exposed(client: TestClient) -> None:
    body = client.get("/api/v1/business").json()
    assert not {"id", "slug", "created_at", "updated_at"} & body.keys()


def test_business_is_chosen_by_server_not_client(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(business_slug="other-business")
    response = client.get("/api/v1/business", params={"slug": "pawcare-demo"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_configured"


def test_database_failure_returns_structured_503(client: TestClient, database_down: None) -> None:
    response = client.get("/api/v1/business")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "secret-password" not in response.text


def test_seed_is_repeatable(db: Session) -> None:
    seed_business(db)
    seed_business(db)
    db.commit()
    count = db.scalar(
        select(func.count()).select_from(Business).where(Business.slug == "pawcare-demo")
    )
    assert count == 1


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers
