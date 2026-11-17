from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import BlockedPeriod, Business, CallbackRequest, StaffSession, StaffUser
from app.staff import upsert_staff
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


def login_at(client: TestClient, slug: str, email: str, password: str = PASSWORD):
    return client.post(f"/api/v1/b/{slug}/staff/login", json={"email": email, "password": password})


def token_for(client: TestClient, staff_user: StaffUser) -> dict:
    slug = "pawcare-demo" if staff_user.email == "maya@pawcare.example" else "other-salon"
    session_token = login_at(client, slug, staff_user.email).json()["token"]
    return {"Authorization": f"Bearer {session_token}"}


# Signing in


def test_login_returns_a_token_that_is_stored_only_as_a_hash(client, db, staff) -> None:
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["staff"] == {
        "email": "maya@pawcare.example",
        "display_name": "Maya",
        "business_slug": "pawcare-demo",
        "business_name": "PawCare Grooming Studio",
    }
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


def test_staff_sign_in_to_their_own_business_by_address(client, staff, other_business_staff):
    response = login_at(client, "other-salon", "owner@other.example")
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    me = client.get(f"{STAFF}/me", headers=headers).json()
    assert me["business_slug"] == "other-salon"
    assert me["business_name"] == "Other Salon"


def test_staff_cannot_sign_in_through_another_business(client, staff, other_business_staff):
    wrong_door = login_at(client, "other-salon", "maya@pawcare.example")
    assert wrong_door.status_code == 401
    assert wrong_door.json()["error"]["code"] == "invalid_credentials"
    # The unprefixed login is the deployment's own business only.
    assert login(client, "owner@other.example").status_code == 401


def test_same_email_at_two_businesses_signs_in_to_the_one_named(
    client, db, staff, other_business_staff
) -> None:
    db.add(
        StaffUser(
            business_id=other_business_staff.business_id,
            email="maya@pawcare.example",
            display_name="Maya at Other",
            password_hash=hasher.hash("a-different-password"),
        )
    )
    db.commit()

    at_other = login_at(client, "other-salon", "maya@pawcare.example", "a-different-password")
    assert at_other.json()["staff"]["business_slug"] == "other-salon"
    assert login_at(client, "other-salon", "maya@pawcare.example").status_code == 401
    at_demo = login_at(client, "pawcare-demo", "maya@pawcare.example")
    assert at_demo.json()["staff"]["business_slug"] == "pawcare-demo"


def test_unknown_business_login_is_404(client) -> None:
    response = login_at(client, "no-such-business", "maya@pawcare.example")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


def test_callbacks_and_metrics_stay_with_their_business(
    client, db, staff, other_business_staff
) -> None:
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    db.add(
        CallbackRequest(
            business_id=staff.business_id, name="Sam", phone="(323) 555-0101", reason="other"
        )
    )
    db.commit()
    own, other = token_for(client, staff), token_for(client, other_business_staff)

    assert len(client.get(f"{STAFF}/callbacks", headers=own).json()) == 1
    assert client.get(f"{STAFF}/callbacks", headers=other).json() == []
    callback_id = client.get(f"{STAFF}/callbacks", headers=own).json()[0]["id"]
    done = client.post(f"{STAFF}/callbacks/{callback_id}/done", headers=other)
    assert done.status_code == 404
    assert client.get(f"{STAFF}/metrics", headers=own).json()["upcoming_bookings"] == 1
    assert client.get(f"{STAFF}/metrics", headers=other).json()["upcoming_bookings"] == 0


# Staff accounts from the command line


def test_upsert_staff_creates_an_account_at_the_named_business(db, other_business_staff):
    upsert_staff(db, "other-salon", " New@Other.Example ", "New", hasher.hash(PASSWORD))
    db.commit()
    created = db.scalar(select(StaffUser).where(StaffUser.email == "new@other.example"))
    assert created.business_id == other_business_staff.business_id
    assert created.display_name == "New"


def test_upsert_staff_updates_an_existing_account(db, staff) -> None:
    upsert_staff(db, "pawcare-demo", "maya@pawcare.example", "Maya R.", hasher.hash("x" * 12))
    db.commit()
    db.expire_all()
    assert db.get(StaffUser, staff.id).display_name == "Maya R."


@pytest.mark.parametrize(
    "slug, password_hash", [("no-such-business", None), ("pawcare-demo", "plain-text")]
)
def test_upsert_staff_rejects_unknown_business_or_unhashed_password(db, slug, password_hash):
    with pytest.raises(ValueError):
        upsert_staff(db, slug, "x@example.com", "X", password_hash or hasher.hash(PASSWORD))


# Metrics


def test_metrics_count_upcoming_new_and_cancelled_bookings(client, db, staff) -> None:
    headers = token_for(client, staff)
    make_booking(db, datetime(2027, 1, 5, 10, 0, tzinfo=LA))
    cancelled = make_booking(db, datetime(2027, 1, 6, 10, 0, tzinfo=LA), key="to-cancel")
    client.post(f"{STAFF}/bookings/{cancelled.reference}/cancel", headers=headers)
    body = client.get(f"{STAFF}/metrics", headers=headers).json()
    assert body["upcoming_bookings"] == 1
    assert body["cancellations_last_7_days"] == 1


def test_metrics_report_voice_reply_times_from_recent_calls(client, db, staff) -> None:
    import uuid

    from app.models import VoiceSession

    def call(started_at, usage) -> None:
        db.add(
            VoiceSession(
                business_id=staff.business_id,
                mode="live",
                client_hash="c" * 64,
                token_hash=uuid.uuid4().hex * 2,
                usage=usage,
                started_at=started_at,
            )
        )

    call(NOW - timedelta(hours=1), {"reply_latency_ms": [900, 1500, 600]})
    call(NOW - timedelta(days=1), {"reply_latency_ms": [700, 2400]})
    call(NOW - timedelta(days=2), None)
    call(NOW - timedelta(days=9), {"reply_latency_ms": [9000]})
    db.commit()
    body = client.get(f"{STAFF}/metrics", headers=token_for(client, staff)).json()
    assert body["voice_replies_measured"] == 5
    assert body["voice_reply_median_ms"] == 900
    assert body["voice_reply_p90_ms"] == 2400


def test_metrics_say_nothing_about_reply_times_before_any_call(client, staff) -> None:
    body = client.get(f"{STAFF}/metrics", headers=token_for(client, staff)).json()
    assert body["voice_replies_measured"] == 0
    assert body["voice_reply_median_ms"] is None
