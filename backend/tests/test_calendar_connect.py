"""Owners connect their own Google Calendar: they share it with HelloEver's service account
and give its ID in their profile, and the app checks it can make changes before connecting."""

import json
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import provision, subscriber_api
from app.config import Settings, get_settings
from app.google_calendar import CalendarAccessProblem, check_access
from app.integrations import CALENDAR, calendar_config, describe, share_calendar
from app.main import app
from app.models import Business, BusinessIntegration
from tests.test_google_calendar import FakeGoogle, account
from tests.test_provision import no_leftover_businesses  # noqa: F401 - autouse cleanup
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
    state: dict = {"problem": None, "checked": []}

    def fake_check(settings: Settings, calendar_id: str) -> None:
        state["checked"].append(calendar_id)
        if state["problem"]:
            raise state["problem"]

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
