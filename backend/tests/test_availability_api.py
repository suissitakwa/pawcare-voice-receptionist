from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import (
    BlockedPeriod,
    Groomer,
    Service,
)
from tests.factories import make_booking

LA = ZoneInfo("America/Los_Angeles")
URL = "/api/v1/availability"
TUESDAY = "2027-01-05"
SATURDAY = "2027-01-09"
MONDAY = "2027-01-11"
# Early enough on Monday 4 January that notice rules do not affect the dates below.
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def query(client: TestClient, day: str = TUESDAY, service="nail-and-paw", size="small"):
    return client.get(URL, params={"service": service, "size": size, "date": day})


def local_starts(body: dict) -> list[str]:
    return [slot["local_start"][11:16] for slot in body["slots"]]


def add_booking(db: Session, start: datetime, minutes: int = 30, status="confirmed") -> None:
    make_booking(db, start, minutes, status)


def test_returns_seeded_hours_break_and_duration(client: TestClient) -> None:
    response = query(client)
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "America/Los_Angeles"
    assert body["duration_minutes"] == 30
    assert body["closed"] is False
    starts = local_starts(body)
    assert starts[0] == "09:00"
    assert starts[-1] == "16:30"
    assert "11:30" in starts
    assert "11:45" not in starts
    assert "12:30" in starts


def test_times_are_utc_with_a_local_equivalent(client: TestClient) -> None:
    slot = query(client).json()["slots"][0]
    assert datetime.fromisoformat(slot["starts_at"]) == datetime(2027, 1, 5, 17, 0, tzinfo=UTC)
    assert slot["starts_at"].endswith("Z")
    assert slot["local_start"] == "2027-01-05T09:00:00-08:00"


def test_duration_depends_on_service_and_size(client: TestClient) -> None:
    body = query(client, service="full-groom", size="large").json()
    assert body["duration_minutes"] == 150
    assert local_starts(body)[-1] == "14:30"
    assert "09:30" in local_starts(body)
    assert "09:45" not in local_starts(body)


def test_groomer_hours_shorten_saturday(client: TestClient) -> None:
    assert local_starts(query(client, SATURDAY).json())[-1] == "14:30"


def test_closed_day_is_reported_as_closed(client: TestClient) -> None:
    body = query(client, MONDAY).json()
    assert body["closed"] is True
    assert body["slots"] == []


def test_existing_booking_and_its_buffer_remove_starts(client: TestClient, db: Session) -> None:
    add_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    starts = local_starts(query(client).json())
    assert "09:15" in starts
    assert "09:30" not in starts
    assert "10:30" not in starts
    assert "10:45" in starts


def test_cancelled_booking_frees_the_slot(client: TestClient, db: Session) -> None:
    add_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA), status="cancelled")
    assert "10:00" in local_starts(query(client).json())


def test_blocked_period_removes_starts(client: TestClient, db: Session) -> None:
    groomer_id = db.scalar(select(Groomer.id))
    db.add(
        BlockedPeriod(
            groomer_id=groomer_id,
            starts_at=datetime(2027, 1, 5, 14, 0, tzinfo=LA),
            ends_at=datetime(2027, 1, 5, 15, 0, tzinfo=LA),
        )
    )
    db.commit()
    starts = local_starts(query(client).json())
    assert "13:30" in starts
    assert "13:45" not in starts
    assert "15:00" in starts


def test_inactive_groomer_means_closed(client: TestClient, db: Session) -> None:
    db.execute(update(Groomer).values(is_active=False))
    db.commit()
    assert query(client).json()["closed"] is True


def test_today_applies_minimum_notice(client: TestClient) -> None:
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 5, 9, 30, tzinfo=LA)
    assert local_starts(query(client).json())[0] == "11:30"


def test_today_is_judged_in_the_business_time_zone(client: TestClient) -> None:
    # 01:00 UTC on 6 January is still the evening of 5 January in Los Angeles.
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 6, 1, 0, tzinfo=UTC)
    assert query(client, TUESDAY).status_code == 200


def test_past_date_is_rejected(client: TestClient) -> None:
    response = query(client, "2027-01-03")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "date_in_past"


def test_booking_horizon_boundary(client: TestClient) -> None:
    assert query(client, "2027-02-03").status_code == 200
    response = query(client, "2027-02-04")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "date_too_far_ahead"


def test_staff_assessment_size_is_routed_to_staff(client: TestClient) -> None:
    response = query(client, size="extra-large")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "staff_assessment_required"


@pytest.mark.parametrize(
    ("service", "size", "code"),
    [("not-a-service", "small", "unknown_service"), ("nail-and-paw", "tiny", "unknown_size")],
)
def test_unknown_inputs_get_structured_404s(client, service, size, code) -> None:
    response = query(client, service=service, size=size)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == code


def test_inactive_service_is_not_bookable(client: TestClient, db: Session) -> None:
    db.execute(update(Service).where(Service.slug == "nail-and-paw").values(is_active=False))
    db.commit()
    assert query(client).json()["error"]["code"] == "unknown_service"


@pytest.mark.parametrize(
    "params",
    [
        {"service": "nail-and-paw", "size": "small"},
        {"service": "nail-and-paw", "size": "small", "date": "next tuesday"},
        {"service": "x" * 65, "size": "small", "date": TUESDAY},
    ],
)
def test_invalid_requests_get_a_structured_error_without_echoing_input(client, params) -> None:
    response = client.get(URL, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "next tuesday" not in response.text


def test_response_contains_no_booking_or_customer_fields(client: TestClient, db: Session) -> None:
    add_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    body = query(client).json()
    assert set(body) == {
        "date",
        "timezone",
        "service",
        "size",
        "duration_minutes",
        "closed",
        "slots",
    }
    assert set(body["slots"][0]) == {"starts_at", "ends_at", "local_start", "local_end"}


def test_database_failure_returns_503(client: TestClient, database_down: None) -> None:
    response = query(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
