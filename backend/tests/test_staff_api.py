from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import BlockedPeriod, Business, StaffSession, StaffUser
from tests.factories import make_booking

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
PASSWORD = "correct-horse-battery"
STAFF = "/api/v1/staff"
hasher = PasswordHasher()


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def business_id(db: Session, slug: str = "pawcare-demo"):
    return db.scalar(select(Business.id).where(Business.slug == slug))


@pytest.fixture
def staff(db: Session) -> StaffUser:
    user = StaffUser(
        business_id=business_id(db),
        email="maya@pawcare.example",
        display_name="Maya",
        password_hash=hasher.hash(PASSWORD),
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def other_business_staff(db: Session) -> StaffUser:
    other = Business(
        slug="other-salon",
        name="Other Salon",
        tagline="",
        about="",
        timezone="America/Los_Angeles",
        contact_email="other@example.com",
        contact_phone="",
        address="",
    )
    db.add(other)
    db.flush()
    user = StaffUser(
        business_id=other.id,
        email="owner@other.example",
        display_name="Other",
        password_hash=hasher.hash(PASSWORD),
    )
    db.add(user)
    db.commit()
    yield user
    db.rollback()
    # Deleting the business cascades to its staff and their sessions.
    db.execute(delete(Business).where(Business.slug == "other-salon"))
    db.commit()


def login(client: TestClient, email: str = "maya@pawcare.example", password: str = PASSWORD):
    return client.post(f"{STAFF}/login", json={"email": email, "password": password})


def token_for(client: TestClient, staff_user: StaffUser) -> dict:
    session_token = None
    # Other businesses sign in through their own deployment, so create the session directly.
    if staff_user.email != "maya@pawcare.example":
        from app.db import get_engine
        from app.staff import sign_in

        with Session(get_engine()) as session:
            session_token, _, _ = sign_in(
                session, staff_user.business_id, staff_user.email, PASSWORD, NOW
            )
            session.commit()
    else:
        session_token = login(client).json()["token"]
    return {"Authorization": f"Bearer {session_token}"}


# Signing in


def test_login_returns_a_token_that_is_stored_only_as_a_hash(client, db, staff) -> None:
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["staff"] == {"email": "maya@pawcare.example", "display_name": "Maya"}
    assert "password" not in response.text
    db.expire_all()
    stored = db.scalar(select(StaffSession.token_hash))
    assert stored != body["token"]
    assert datetime.fromisoformat(body["expires_at"]) == NOW.astimezone(UTC) + timedelta(hours=8)


def test_email_is_case_insensitive(client, staff) -> None:
    assert login(client, email="  Maya@PawCare.example ").status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("maya@pawcare.example", "wrong-password"), ("nobody@pawcare.example", PASSWORD)],
)
def test_wrong_email_and_wrong_password_look_identical(client, staff, email, password) -> None:
    response = login(client, email, password)
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "invalid_credentials", "message": "That email and password do not match."}
    }


def test_account_locks_after_five_failures_and_unlocks_later(client, staff) -> None:
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401
    locked = login(client)
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=16)
    assert login(client).status_code == 200


def test_success_resets_the_failure_count(client, db, staff) -> None:
    for _ in range(4):
        login(client, password="wrong")
    assert login(client).status_code == 200
    for _ in range(4):
        login(client, password="wrong")
    assert login(client).status_code == 200


# Every staff route requires a valid session.

PROTECTED = [
    ("get", "/me"),
    ("get", "/bookings"),
    ("post", "/bookings/PC-ANYTHING/cancel"),
    ("get", "/blocks"),
    ("post", "/blocks"),
    ("delete", "/blocks/00000000-0000-0000-0000-000000000000"),
    ("get", "/metrics"),
    ("post", "/logout"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
@pytest.mark.parametrize(
    "header", [None, "Bearer", "Bearer not-a-real-token", "Basic bWF5YTpwYXNz", "token"]
)
def test_staff_routes_reject_missing_or_invalid_tokens(client, staff, method, path, header) -> None:
    headers = {"Authorization": header} if header else {}
    response = getattr(client, method)(f"{STAFF}{path}", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_expired_session_is_rejected(client, staff) -> None:
    headers = token_for(client, staff)
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=8, seconds=1)
    assert client.get(f"{STAFF}/me", headers=headers).status_code == 401


def test_logout_revokes_the_session(client, staff) -> None:
    headers = token_for(client, staff)
    assert client.post(f"{STAFF}/logout", headers=headers).status_code == 204
    assert client.get(f"{STAFF}/me", headers=headers).status_code == 401


# Bookings


def test_lists_upcoming_bookings_with_contact_details(client, db, staff) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    make_booking(db, datetime(2027, 1, 1, 10, 0, tzinfo=LA), key="past")
    bookings = client.get(f"{STAFF}/bookings", headers=token_for(client, staff)).json()
    assert len(bookings) == 1
    assert bookings[0]["owner_email"] == "another@example.com"
    assert bookings[0]["local_start"] == "2027-01-05T10:00:00-08:00"
    assert bookings[0]["service"] == "Nail and Paw Care"


def test_lists_past_bookings_separately(client, db, staff) -> None:
    make_booking(db, datetime(2027, 1, 1, 10, 0, tzinfo=LA), key="past")
    headers = token_for(client, staff)
    past = client.get(f"{STAFF}/bookings", params={"scope": "past"}, headers=headers).json()
    assert [b["local_start"][:10] for b in past] == ["2027-01-01"]


def test_cancelling_frees_the_slot_for_customers(client, db, staff) -> None:
    booking = make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    headers = token_for(client, staff)
    params = {"service": "nail-and-paw", "size": "small", "date": "2027-01-05"}

    def starts() -> list[str]:
        slots = client.get("/api/v1/availability", params=params).json()["slots"]
        return [slot["local_start"][11:16] for slot in slots]

    assert "10:00" not in starts()
    response = client.post(f"{STAFF}/bookings/{booking.reference}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled_at"] is not None
    assert "10:00" in starts()


def test_cancelling_twice_is_harmless(client, db, staff) -> None:
    booking = make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    headers = token_for(client, staff)
    first = client.post(f"{STAFF}/bookings/{booking.reference}/cancel", headers=headers).json()
    second = client.post(f"{STAFF}/bookings/{booking.reference}/cancel", headers=headers).json()
    assert second == first


def test_cancelling_an_unknown_booking_is_not_found(client, staff) -> None:
    response = client.post(f"{STAFF}/bookings/PC-NOPE/cancel", headers=token_for(client, staff))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "booking_not_found"


# Blocks


def block(client, headers, **overrides):
    body = {
        "start_date": "2027-01-05",
        "start_time": "13:00",
        "end_date": "2027-01-05",
        "end_time": "15:00",
        "reason": "Vet visit",
    }
    body.update(overrides)
    return client.post(f"{STAFF}/blocks", json=body, headers=headers)


def test_block_uses_business_wall_clock_and_hides_times(client, db, staff) -> None:
    headers = token_for(client, staff)
    response = block(client, headers)
    assert response.status_code == 201
    created = response.json()["block"]
    assert datetime.fromisoformat(created["starts_at"]) == datetime(2027, 1, 5, 21, tzinfo=UTC)
    assert created["local_start"] == "2027-01-05T13:00:00-08:00"
    slots = client.get(
        "/api/v1/availability",
        params={"service": "nail-and-paw", "size": "small", "date": "2027-01-05"},
    ).json()["slots"]
    starts = [slot["local_start"][11:16] for slot in slots]
    assert "12:30" in starts
    assert "13:00" not in starts
    assert "15:00" in starts


def test_block_converts_summer_time_correctly(client, staff) -> None:
    app.dependency_overrides[get_now] = lambda: datetime(2027, 7, 1, 8, tzinfo=LA)
    created = block(
        client, token_for(client, staff), start_date="2027-07-06", end_date="2027-07-06"
    ).json()["block"]
    assert datetime.fromisoformat(created["starts_at"]) == datetime(2027, 7, 6, 20, tzinfo=UTC)


def test_block_reports_bookings_it_overlaps(client, db, staff) -> None:
    make_booking(db, datetime(2027, 1, 5, 14, 0, tzinfo=LA))
    body = block(client, token_for(client, staff)).json()
    assert [c["local_start"][11:16] for c in body["conflicts"]] == ["14:00"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"end_time": "13:00"},
        {"end_date": "2027-01-04"},
        {"start_date": "2027-01-01", "end_date": "2027-01-02"},
        {"end_date": "2027-03-01"},
        {"reason": "x" * 201},
    ],
)
def test_invalid_blocks_are_rejected(client, staff, overrides) -> None:
    response = block(client, token_for(client, staff), **overrides)
    assert response.status_code == 422
    assert response.json()["error"]["code"] in ("invalid_block", "invalid_request")


def test_blocks_can_be_listed_and_removed(client, db, staff) -> None:
    headers = token_for(client, staff)
    block_id = block(client, headers).json()["block"]["id"]
    assert [b["id"] for b in client.get(f"{STAFF}/blocks", headers=headers).json()] == [block_id]
    assert client.delete(f"{STAFF}/blocks/{block_id}", headers=headers).status_code == 204
    assert client.get(f"{STAFF}/blocks", headers=headers).json() == []
    assert client.delete(f"{STAFF}/blocks/{block_id}", headers=headers).status_code == 404


# One business's staff cannot reach another business's data.


def test_staff_cannot_see_or_change_another_business(client, db, staff, other_business_staff):
    booking = make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    own = token_for(client, staff)
    block_id = block(client, own).json()["block"]["id"]
    other = token_for(client, other_business_staff)

    assert client.get(f"{STAFF}/bookings", headers=other).json() == []
    assert client.get(f"{STAFF}/blocks", headers=other).json() == []
    cancel = client.post(f"{STAFF}/bookings/{booking.reference}/cancel", headers=other)
    assert cancel.status_code == 404
    assert client.delete(f"{STAFF}/blocks/{block_id}", headers=other).status_code == 404
    db.expire_all()
    assert db.get(BlockedPeriod, block_id) is not None
    assert client.get(f"{STAFF}/bookings", headers=own).json()[0]["status"] == "confirmed"


# Metrics


def test_metrics_count_upcoming_new_and_cancelled_bookings(client, db, staff) -> None:
    headers = token_for(client, staff)
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    cancelled = make_booking(db, datetime(2027, 1, 6, 10, 0, tzinfo=LA), key="to-cancel")
    client.post(f"{STAFF}/bookings/{cancelled.reference}/cancel", headers=headers)
    body = client.get(f"{STAFF}/metrics", headers=headers).json()
    assert body["upcoming_bookings"] == 1
    assert body["cancellations_last_7_days"] == 1
