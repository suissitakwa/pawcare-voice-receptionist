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


def phone_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "phone_enabled": True,
        "voice_mode": "live",
        "openai_api_key": "test-key-not-real",
        "phone_number": "+12605550123",
    }
    values.update(overrides)
    return Settings(**values)


def test_assistant_phone_shown_while_phone_line_answers(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: phone_settings()
    assert client.get("/api/v1/business").json()["assistant_phone"] == "+12605550123"


def test_assistant_phone_hidden_when_calls_would_be_declined(client: TestClient) -> None:
    assert client.get("/api/v1/business").json()["assistant_phone"] is None
    for overrides in ({"phone_enabled": False}, {"voice_mode": "test"}, {"phone_number": ""}):
        app.dependency_overrides[get_settings] = lambda o=overrides: phone_settings(**o)
        assert client.get("/api/v1/business").json()["assistant_phone"] is None


def test_phone_number_must_be_e164() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(phone_number="(260) 555-0123")
    assert Settings(phone_number=" +12605550123 ").phone_number == "+12605550123"
