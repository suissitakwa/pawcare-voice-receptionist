from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.main import describe_database_error, expected_revision


def test_liveness_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_does_not_need_database(client: TestClient, database_down: None) -> None:
    assert client.get("/health").status_code == 200


def test_readiness_reports_database_and_revision(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "schema_revision": expected_revision(),
    }


def test_readiness_returns_503_without_leaking_connection_details(
    client: TestClient, database_down: None
) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"
    assert "secret-password" not in response.text
    assert "127.0.0.1" not in response.text


def test_readiness_returns_503_when_schema_is_behind(client: TestClient, db: Session) -> None:
    db.execute(text("UPDATE alembic_version SET version_num = 'stale'"))
    db.commit()
    try:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["schema_revision"] == "stale"
    finally:
        db.execute(
            text("UPDATE alembic_version SET version_num = :rev"), {"rev": expected_revision()}
        )
        db.commit()


def test_database_error_logging_keeps_row_data_out_of_logs() -> None:
    driver_error = Exception("duplicate key: Key (email)=(owner@example.com) already exists")
    logged = describe_database_error(IntegrityError("INSERT", {}, driver_error))
    assert logged == "IntegrityError"
    assert "owner@example.com" not in logged


def test_database_error_logging_explains_connection_failures() -> None:
    driver_error = Exception("connection timeout expired")
    logged = describe_database_error(OperationalError("SELECT 1", {}, driver_error))
    assert "connection timeout expired" in logged
