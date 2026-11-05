from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import BlockedPeriod, Booking, BookingDraft, Groomer
from tests.factories import make_booking

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
PREPARE = "/api/v1/bookings/prepare"
CONFIRM = "/api/v1/bookings/confirm"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def details(**overrides) -> dict:
    body = {
        "service": "full-groom",
        "size": "medium",
        "starts_at": NINE.isoformat(),
        "owner_name": "Jordan Example",
        "owner_email": "jordan@example.com",
        "pet_name": "Biscuit",
    }
    body.update(overrides)
    return body


def prepare(client: TestClient, **overrides):
    return client.post(PREPARE, json=details(**overrides))


def confirm(client: TestClient, prepared: dict, key: str = "key-0001-abcdef", **overrides):
    body = {
        "confirmation_token": prepared["confirmation_token"],
        "summary_hash": prepared["summary_hash"],
    }
    body.update(overrides)
    return client.post(CONFIRM, json=body, headers={"Idempotency-Key": key})


def count(db: Session, model=Booking) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


# Preparing


def test_prepare_returns_summary_from_database_rules(client: TestClient, db: Session) -> None:
    response = prepare(client)
    assert response.status_code == 201
    body = response.json()
    summary = body["summary"]
    assert summary["service"] == "Full Groom"
    assert summary["size"] == "Medium"
    assert summary["duration_minutes"] == 120
    assert summary["price_cents"] == 9000
    assert summary["local_start"] == "2027-01-05T09:00:00-08:00"
    assert summary["local_end"] == "2027-01-05T11:00:00-08:00"
    assert datetime.fromisoformat(summary["starts_at"]) == datetime(2027, 1, 5, 17, tzinfo=UTC)
    assert len(body["summary_hash"]) == 64
    assert count(db) == 0


def test_client_supplied_price_or_duration_is_rejected(client: TestClient) -> None:
    response = prepare(client, price_cents=1)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_token_is_stored_only_as_a_hash(client: TestClient, db: Session) -> None:
    token = prepare(client).json()["confirmation_token"]
    stored = db.scalar(select(BookingDraft.token_hash))
    assert stored != token
    assert token not in stored


@pytest.mark.parametrize(
    "start",
    [
        datetime(2027, 1, 5, 9, 7, tzinfo=LA),  # Not on the 15-minute grid.
        datetime(2027, 1, 5, 11, 0, tzinfo=LA),  # A 2-hour groom would run into the break.
        datetime(2027, 1, 5, 15, 15, tzinfo=LA),  # Would end after closing.
        datetime(2027, 1, 11, 9, 0, tzinfo=LA),  # Closed on Mondays.
    ],
)
def test_prepare_rejects_starts_the_rules_do_not_allow(client: TestClient, start) -> None:
    response = prepare(client, starts_at=start.isoformat())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_unavailable"


def test_prepare_rejects_a_taken_slot(client: TestClient, db: Session) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    assert prepare(client).json()["error"]["code"] == "slot_unavailable"


def test_prepare_requires_notice(client: TestClient) -> None:
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 5, 7, 30, tzinfo=LA)
    assert prepare(client).json()["error"]["code"] == "slot_unavailable"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"size": "extra-large"}, "staff_assessment_required"),
        ({"service": "spa-day"}, "unknown_service"),
        ({"starts_at": "2027-01-05T09:00:00"}, "invalid_request"),
        ({"owner_email": None}, "invalid_request"),
        ({"owner_email": "not-an-email"}, "invalid_request"),
        ({"owner_email": None, "owner_phone": "call me"}, "invalid_request"),
        ({"pet_name": ""}, "invalid_request"),
        ({"owner_name": "x" * 81}, "invalid_request"),
        ({"starts_at": "2027-03-01T09:00:00-08:00"}, "date_too_far_ahead"),
    ],
)
def test_prepare_validates_input(client: TestClient, overrides, code) -> None:
    response = prepare(client, **overrides)
    assert response.status_code in (404, 422)
    assert response.json()["error"]["code"] == code


def test_phone_alone_is_enough_contact(client: TestClient) -> None:
    response = prepare(client, owner_email=None, owner_phone="(323) 555-0100")
    assert response.status_code == 201


# Confirming


def test_confirm_creates_one_booking_with_a_reference(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    response = confirm(client, prepared)
    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("PC-")
    assert body["status"] == "confirmed"
    assert body["summary"] == prepared["summary"]
    assert count(db) == 1
    booking = db.scalar(select(Booking))
    assert booking.pet_name == "Biscuit"
    assert booking.occupied_until - booking.ends_at == timedelta(minutes=15)


def test_confirmed_slot_disappears_from_availability(client: TestClient) -> None:
    confirm(client, prepare(client).json())
    slots = client.get(
        "/api/v1/availability",
        params={"service": "full-groom", "size": "medium", "date": "2027-01-05"},
    ).json()["slots"]
    starts = [slot["local_start"][11:16] for slot in slots]
    assert "09:00" not in starts
    assert "10:00" not in starts


def test_confirm_requires_an_idempotency_key(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    body = {k: prepared[k] for k in ("confirmation_token", "summary_hash")}
    response = client.post(CONFIRM, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert count(db) == 0


def test_replay_with_same_key_returns_the_original_booking(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    first = confirm(client, prepared)
    second = confirm(client, prepared)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    assert count(db) == 1


def test_replay_works_even_after_the_summary_expires(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    first = confirm(client, prepared)
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=2)
    assert confirm(client, prepared).json()["reference"] == first.json()["reference"]


def test_same_key_for_a_different_request_is_rejected(client: TestClient, db: Session) -> None:
    confirm(client, prepare(client).json())
    other = prepare(client, starts_at=datetime(2027, 1, 5, 12, 30, tzinfo=LA).isoformat()).json()
    response = confirm(client, other)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert count(db) == 1


def test_a_summary_can_be_confirmed_only_once(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    confirm(client, prepared, key="key-first-0001")
    response = confirm(client, prepared, key="key-second-0002")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_confirmed"
    assert count(db) == 1


def test_confirmation_is_bound_to_the_summary_that_was_shown(client: TestClient, db) -> None:
    prepared = prepare(client).json()
    other_hash = prepare(client, pet_name="Nala").json()["summary_hash"]
    response = confirm(client, prepared, summary_hash=other_hash)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "confirmation_mismatch"
    assert count(db) == 0


def test_changed_details_need_a_new_summary(client: TestClient) -> None:
    original = prepare(client).json()
    changed = prepare(client, pet_name="Nala").json()
    assert changed["summary_hash"] != original["summary_hash"]
    assert changed["confirmation_token"] != original["confirmation_token"]


def test_expired_summary_cannot_be_confirmed(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=10)
    response = confirm(client, prepared)
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "confirmation_expired"
    assert count(db) == 0


def test_unknown_token_is_not_found(client: TestClient) -> None:
    response = confirm(client, {"confirmation_token": "x" * 43, "summary_hash": "0" * 64})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "confirmation_not_found"


def test_slot_taken_after_prepare_gives_a_clear_conflict(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    response = confirm(client, prepared)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_unavailable"
    assert count(db) == 1


def test_block_added_after_prepare_is_respected(client: TestClient, db: Session) -> None:
    prepared = prepare(client).json()
    db.add(
        BlockedPeriod(
            groomer_id=db.scalar(select(Groomer.id)),
            starts_at=datetime(2027, 1, 5, 10, 0, tzinfo=LA),
            ends_at=datetime(2027, 1, 5, 10, 30, tzinfo=LA),
        )
    )
    db.commit()
    assert confirm(client, prepared).json()["error"]["code"] == "slot_unavailable"
    assert count(db) == 0


def test_no_endpoint_lists_or_reads_bookings(client: TestClient) -> None:
    confirm(client, prepare(client).json())
    paths = {route.path for route in app.routes}
    assert not any(
        path.startswith("/api/v1/bookings") and "prepare" not in path and "confirm" not in path
        for path in paths
    )
    assert client.get("/api/v1/bookings").status_code in (404, 405)


def test_database_failure_returns_503(client: TestClient, database_down: None) -> None:
    response = prepare(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


# The database guarantee itself, without the API's earlier checks.


def test_database_rejects_overlapping_confirmed_bookings(db: Session) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    with pytest.raises(IntegrityError, match="bookings_no_overlap"):
        make_booking(db, datetime(2027, 1, 5, 10, 30, tzinfo=LA), key="overlap")
    db.rollback()


def test_database_counts_the_buffer_as_occupied(db: Session) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))  # Occupied until 10:45.
    with pytest.raises(IntegrityError, match="bookings_no_overlap"):
        make_booking(db, datetime(2027, 1, 5, 10, 40, tzinfo=LA), key="in-buffer")
    db.rollback()
    make_booking(db, datetime(2027, 1, 5, 10, 45, tzinfo=LA), key="after-buffer")


def test_database_allows_overlap_with_a_cancelled_booking(db: Session) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA), status="cancelled")
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA), key="rebook")
    assert count(db) == 2
