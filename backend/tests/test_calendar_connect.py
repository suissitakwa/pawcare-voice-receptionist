"""Owners connect their own Google Calendar: they share it with HelloEver's service account
and give its ID in their profile, and the app checks it can make changes before connecting."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import google_calendar, provision, subscriber_api
from app.config import Settings, get_settings
from app.google_calendar import CalendarAccessProblem, calendar_status, check_access
from app.integrations import CALENDAR, calendar_config, describe, remove, share_calendar
from app.main import app
from app.models import Business, BusinessIntegration, StaffUser
from tests.factories import make_booking
from tests.test_google_calendar import LA, NINE, NOW, FakeGoogle, account
from tests.test_provision import no_leftover_businesses  # noqa: F401 - autouse cleanup
from tests.test_staff_api import PASSWORD, login
from tests.test_subscribers import API, auth, complete

KEY = Fernet.generate_key().decode()
EMAIL = "sync@project.iam.gserviceaccount.com"
CALENDAR_ID = "owner@gmail.com"
LIST = "/users/me/calendarList"


def settings_with_account() -> Settings:
    key, _ = account()
    return Settings(
        integrations_key=KEY,
        google_service_account_json=json.dumps(key),
        webhook_worker_enabled=False,
    )


SETTINGS = settings_with_account()


@pytest.fixture(autouse=True)
def configured(db: Session, monkeypatch) -> Iterator[None]:
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    monkeypatch.setattr(provision, "get_settings", lambda: SETTINGS)
    db.execute(delete(BusinessIntegration))
    db.commit()
    yield
    db.rollback()
    db.execute(delete(BusinessIntegration))
    db.commit()
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def google(monkeypatch) -> dict:
    """What the fake access check answers: None for writable, or a problem to raise."""
    state: dict = {"problem": None, "checked": [], "name": None}

    def fake_check(settings: Settings, calendar_id: str) -> str | None:
        state["checked"].append(calendar_id)
        if state["problem"]:
            raise state["problem"]
        return state["name"]

    monkeypatch.setattr(subscriber_api, "check_access", fake_check)
    return state


# The access check against Google


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ((404, None), "calendar_not_shared"),
        ((403, None), "calendar_not_shared"),
        ((200, {"accessRole": "freeBusyReader"}), "calendar_read_only"),
        ((200, {"accessRole": "reader"}), "calendar_read_only"),
        ((500, None), "calendar_unreachable"),
    ],
)
def test_the_check_explains_what_is_wrong_with_the_sharing(response, code) -> None:
    send = FakeGoogle({("POST", LIST): response})
    with pytest.raises(CalendarAccessProblem) as problem:
        check_access(SETTINGS, CALENDAR_ID, send)
    assert problem.value.code == code
    if code != "calendar_unreachable":
        assert EMAIL in problem.value.message


@pytest.mark.parametrize("role", ["writer", "owner"])
def test_a_calendar_shared_to_make_changes_passes(role) -> None:
    send = FakeGoogle({("POST", LIST): (200, {"id": CALENDAR_ID, "accessRole": role})})
    check_access(SETTINGS, CALENDAR_ID, send)
    method, url, body, _ = send.requests[-1]
    assert (method, json.loads(body)) == ("POST", {"id": CALENDAR_ID})


def test_a_calendar_already_in_the_list_is_looked_up() -> None:
    send = FakeGoogle(
        {
            ("POST", LIST): (409, None),
            ("GET", f"{LIST}/owner%40gmail.com"): (200, {"accessRole": "writer"}),
        }
    )
    check_access(SETTINGS, CALENDAR_ID, send)


def test_nothing_is_offered_without_a_service_account() -> None:
    with pytest.raises(CalendarAccessProblem) as problem:
        check_access(Settings(google_service_account_json=""), CALENDAR_ID, FakeGoogle({}))
    assert problem.value.code == "calendar_not_offered"


# The profile


def test_options_show_the_address_to_share_with(client) -> None:
    assert client.get(f"{API}/options").json()["calendar_share_email"] == EMAIL
    app.dependency_overrides[get_settings] = lambda: Settings(google_service_account_json="")
    assert client.get(f"{API}/options").json()["calendar_share_email"] is None


def test_connecting_checks_access_then_saves_the_calendar(client, google) -> None:
    headers = auth(client)
    response = client.post(
        f"{API}/profile/calendar", headers=headers, json={"calendar_id": " owner@gmail.com "}
    )
    assert response.status_code == 200, response.text
    details = response.json()["details"]
    assert details["google_calendar_id"] == CALENDAR_ID
    assert details["google_calendar_connected"] is True
    assert details["calendar_provider"] == "google"
    assert google["checked"] == [CALENDAR_ID]


def test_a_calendar_that_is_not_shared_is_not_saved(client, google) -> None:
    headers = auth(client)
    google["problem"] = CalendarAccessProblem("calendar_not_shared", f"Share it with {EMAIL}")
    response = client.post(
        f"{API}/profile/calendar", headers=headers, json={"calendar_id": "x@y.z"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "calendar_not_shared"
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["google_calendar_connected"] is False


def test_a_profile_edit_cannot_mark_a_calendar_connected(client) -> None:
    headers = auth(client)
    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={"details": {"google_calendar_id": "x@y.z", "google_calendar_connected": True}},
    )
    assert response.status_code == 200
    assert response.json()["details"]["google_calendar_connected"] is False
    assert response.json()["details"]["google_calendar_id"] is None


def live_business(client, db: Session, google) -> tuple[dict, str]:
    headers = auth(client)
    complete(client, headers)
    client.post(f"{API}/profile/submit", headers=headers)
    client.post(f"{API}/profile/calendar", headers=headers, json={"calendar_id": CALENDAR_ID})
    db.expire_all()
    seed, _, steps = provision.approve(db, "sam@example.com")
    db.commit()
    assert not any("calendar" in step.lower() for step in steps), steps
    return headers, seed.slug


def test_a_calendar_connected_at_sign_up_is_used_once_live(client, db, google) -> None:
    _, slug = live_business(client, db, google)
    business_id = db.scalar(select(Business.id).where(Business.slug == slug))
    config = calendar_config(db, SETTINGS, business_id)
    assert config.calendar_id == CALENDAR_ID
    # HelloEver's key from the environment, never a copy in the database.
    assert config.service_account_json == SETTINGS.google_service_account_json
    assert "shared with HelloEver" in describe(db, slug)[0]


def test_a_live_business_connects_and_disconnects_at_once(client, db, google) -> None:
    headers, slug = live_business(client, db, google)
    business_id = db.scalar(select(Business.id).where(Business.slug == slug))

    client.post(f"{API}/profile/calendar", headers=headers, json={"calendar_id": "team@gmail.com"})
    db.expire_all()
    assert calendar_config(db, SETTINGS, business_id).calendar_id == "team@gmail.com"

    response = client.delete(f"{API}/profile/calendar", headers=headers)
    assert response.status_code == 200
    assert response.json()["details"]["google_calendar_connected"] is False
    db.expire_all()
    assert calendar_config(db, SETTINGS, business_id) is None
    assert not db.scalar(select(BusinessIntegration).where(BusinessIntegration.kind == CALENDAR))


def test_an_unconnected_google_calendar_is_left_as_a_step(client, db) -> None:
    headers = auth(client)
    complete(client, headers)
    client.post(f"{API}/profile/submit", headers=headers)
    db.expire_all()
    _, _, steps = provision.approve(db, "sam@example.com")
    assert any("share-calendar" in step for step in steps)


def test_a_shared_calendar_needs_the_service_account_key(db) -> None:
    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        share_calendar(db, Settings(integrations_key=KEY), "pawcare-demo", CALENDAR_ID)


# Status once connected


@pytest.fixture
def access(monkeypatch) -> dict:
    """What the live check against Google answers for a connected calendar."""
    state: dict = {"problem": None, "checked": [], "name": None}

    def fake_check(account_json: str, calendar_id: str) -> str | None:
        state["checked"].append((json.loads(account_json)["client_email"], calendar_id))
        if state["problem"]:
            raise state["problem"]
        return state["name"]

    monkeypatch.setattr(google_calendar, "check_account_access", fake_check)
    return state


def demo_id(db: Session):
    return db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))


def staff_headers(client, db: Session) -> dict:
    db.add(
        StaffUser(
            business_id=demo_id(db),
            email="maya@pawcare.example",
            display_name="Maya",
            password_hash=PasswordHasher().hash(PASSWORD),
        )
    )
    db.commit()
    return {"Authorization": f"Bearer {login(client).json()['token']}"}


def test_a_business_without_a_calendar_says_so(db, access) -> None:
    status = calendar_status(db, SETTINGS, demo_id(db))
    assert (status.connected, status.calendar_id, status.problem) == (False, None, None)
    assert access["checked"] == []


def test_a_working_calendar_is_checked_live_and_shows_its_sync(db, access) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    synced = make_booking(db, NINE)
    failing = make_booking(db, NINE + timedelta(hours=2))
    synced.calendar_dirty = False
    synced.calendar_synced_at = NOW
    failing.calendar_error = "saving the event returned HTTP 403"
    failing.calendar_next_attempt_at = NOW
    db.commit()

    status = calendar_status(db, SETTINGS, demo_id(db))
    assert status.connected and status.calendar_id == CALENDAR_ID
    assert status.problem is None and status.problem_code is None
    assert access["checked"] == [(EMAIL, CALENDAR_ID)]
    assert status.waiting == 1
    assert status.last_synced_at == NOW
    assert "403" in status.last_error


def test_a_calendar_that_was_unshared_shows_why(db, access) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    access["problem"] = CalendarAccessProblem("calendar_not_shared", f"Share it with {EMAIL}")
    status = calendar_status(db, SETTINGS, demo_id(db))
    assert status.connected
    assert status.problem_code == "calendar_not_shared"
    assert EMAIL in status.problem


def test_settings_that_cannot_be_read_pause_booking_and_say_so(db, access) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    # The shared account's key was removed from the environment.
    broken = Settings(integrations_key=KEY, google_service_account_json="")
    status = calendar_status(db, broken, demo_id(db))
    assert status.connected and status.problem_code == "calendar_settings_unreadable"
    assert "paused" in status.problem
    assert access["checked"] == []


def test_staff_see_their_own_calendar_status(client, db, access, second_business) -> None:
    headers = staff_headers(client, db)
    assert client.get("/api/v1/staff/calendar", headers=headers).json()["connected"] is False
    # Another business's calendar isn't this business's.
    share_calendar(db, SETTINGS, second_business, "other@gmail.com")
    db.commit()
    assert client.get("/api/v1/staff/calendar", headers=headers).json()["connected"] is False

    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    access["problem"] = CalendarAccessProblem("calendar_read_only", "only to see it")
    body = client.get("/api/v1/staff/calendar", headers=headers).json()
    assert body["calendar_id"] == CALENDAR_ID
    assert body["problem_code"] == "calendar_read_only"
    assert "private_key" not in json.dumps(body)
    assert client.get("/api/v1/staff/calendar").status_code == 401


# A different calendar gets the upcoming bookings


def test_connecting_a_different_calendar_writes_upcoming_bookings_again(db) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    upcoming = make_booking(db, NINE)
    cancelled = make_booking(db, NINE + timedelta(hours=1), status="cancelled")
    past = make_booking(db, datetime(2020, 1, 6, 9, 0, tzinfo=LA))
    for booking in (upcoming, cancelled, past):
        booking.calendar_dirty = False
        booking.calendar_attempts = 3
    db.commit()

    # Saving the same calendar again changes nothing.
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    db.refresh(upcoming)
    assert upcoming.calendar_dirty is False

    share_calendar(db, SETTINGS, "pawcare-demo", "team@gmail.com")
    db.commit()
    for booking in (upcoming, cancelled, past):
        db.refresh(booking)
    assert upcoming.calendar_dirty is True and upcoming.calendar_attempts == 0
    assert upcoming.calendar_next_attempt_at is None
    assert cancelled.calendar_dirty is False
    assert past.calendar_dirty is False


def test_reconnecting_after_a_disconnect_writes_upcoming_bookings_again(db) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    booking = make_booking(db, NINE)
    booking.calendar_dirty = False
    db.commit()
    remove(db, "pawcare-demo", CALENDAR)
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    db.refresh(booking)
    assert booking.calendar_dirty is True


# The owner checks the connection from their profile


def test_the_profile_checks_a_connected_calendar(client, google) -> None:
    headers = auth(client)
    check = f"{API}/profile/calendar/check"
    response = client.post(check, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "calendar_not_connected"

    client.post(f"{API}/profile/calendar", headers=headers, json={"calendar_id": CALENDAR_ID})
    assert client.post(check, headers=headers).json() == {
        "ok": True,
        "code": None,
        "message": None,
        "name": None,
    }

    google["problem"] = CalendarAccessProblem("calendar_not_shared", f"Share it with {EMAIL}")
    body = client.post(check, headers=headers).json()
    assert body["ok"] is False and body["code"] == "calendar_not_shared"
    assert EMAIL in body["message"]
    # A failed check disconnects nothing.
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["google_calendar_connected"] is True


# The calendar's name, shown instead of its id


def test_the_check_returns_the_calendar_name_as_its_owner_sees_it() -> None:
    send = FakeGoogle(
        {
            ("POST", LIST): (
                200,
                {"id": CALENDAR_ID, "accessRole": "writer", "summary": " Shaggy Chic bookings "},
            )
        }
    )
    assert check_access(SETTINGS, CALENDAR_ID, send) == "Shaggy Chic bookings"
    nameless = FakeGoogle({("POST", LIST): (200, {"id": CALENDAR_ID, "accessRole": "owner"})})
    assert check_access(SETTINGS, CALENDAR_ID, nameless) is None


def test_connecting_keeps_the_name_and_a_check_refreshes_it(client, google) -> None:
    headers = auth(client)
    google["name"] = "Shaggy Chic bookings"
    client.post(f"{API}/profile/calendar", headers=headers, json={"calendar_id": CALENDAR_ID})
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["google_calendar_name"] == "Shaggy Chic bookings"

    google["name"] = "Shaggy Chic appointments"
    body = client.post(f"{API}/profile/calendar/check", headers=headers).json()
    assert body["name"] == "Shaggy Chic appointments"
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["google_calendar_name"] == "Shaggy Chic appointments"

    client.delete(f"{API}/profile/calendar", headers=headers)
    details = client.get(f"{API}/profile", headers=headers).json()["details"]
    assert details["google_calendar_name"] is None


def test_a_profile_edit_cannot_set_the_calendar_name(client) -> None:
    headers = auth(client)
    client.patch(
        f"{API}/profile", headers=headers, json={"details": {"google_calendar_name": "Fake"}}
    )
    assert (
        client.get(f"{API}/profile", headers=headers).json()["details"]["google_calendar_name"]
        is None
    )


def test_the_dashboard_status_names_the_calendar(db, access) -> None:
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    access["name"] = "Shaggy Chic bookings"
    status = calendar_status(db, SETTINGS, demo_id(db))
    assert status.calendar_name == "Shaggy Chic bookings"
    access["problem"] = CalendarAccessProblem("calendar_not_shared", "Share it")
    assert calendar_status(db, SETTINGS, demo_id(db)).calendar_name is None
