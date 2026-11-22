"""Owners sign in with Google once, pick one of their calendars, and it's shared with
HelloEver's service account and connected. Google is replaced by a fake."""

import json
import urllib.parse
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app import google_oauth, subscriber_api
from app.config import Settings, get_settings
from app.google_calendar import API, CalendarAccessProblem
from app.main import app
from tests.test_calendar_connect import EMAIL, KEY
from tests.test_google_calendar import account
from tests.test_subscribers import API as SUBSCRIBERS
from tests.test_subscribers import auth

GOOGLE = f"{SUBSCRIBERS}/profile/calendar/google"
SCOPE = " ".join(google_oauth.SCOPES)
CALENDARS = {
    "items": [
        {"id": "team@group.calendar.google.com", "summary": "Team"},
        {"id": "sam@gmail.com", "summary": "Sam Lee", "primary": True},
        {"id": "old@group.calendar.google.com", "summary": "Old", "deleted": True},
    ]
}


def settings(**overrides) -> Settings:
    key, _ = account()
    values = {
        "integrations_key": KEY,
        "google_service_account_json": json.dumps(key),
        "google_oauth_client_id": "client-1.apps.googleusercontent.com",
        "google_oauth_client_secret": "client-secret",
        "public_site_url": "https://site.example/",
        "webhook_worker_enabled": False,
        **overrides,
    }
    return Settings(**values)


SETTINGS = settings()


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes | None, dict]] = []
        self.token_reply: tuple[int, dict | None] = (
            200,
            {"access_token": "user-token-1", "scope": SCOPE, "expires_in": 3599},
        )
        self.acl_status = 200

    def __call__(self, method: str, url: str, body: bytes | None, headers: dict):
        self.requests.append((method, url, body, headers))
        if url == google_oauth.TOKEN_URL:
            return self.token_reply
        if url.startswith(f"{API}/users/me/calendarList"):
            return 200, CALENDARS
        if "/acl" in url:
            return self.acl_status, {"id": "rule"} if self.acl_status == 200 else None
        return 404, None


@pytest.fixture(autouse=True)
def configured(monkeypatch) -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    monkeypatch.setattr(google_oauth, "transport", fake)
    yield fake
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def checks(monkeypatch) -> list:
    """The access check after sharing: each entry is None (passes) or a problem to raise."""
    answers: list = []

    def fake_check(_settings: Settings, calendar_id: str) -> None:
        answer = answers.pop(0) if answers else None
        if answer:
            raise answer

    monkeypatch.setattr(subscriber_api, "check_access", fake_check)
    monkeypatch.setattr(subscriber_api, "SHARING_SETTLES_SECONDS", 0)
    return answers


def start(client, headers) -> dict:
    response = client.post(f"{GOOGLE}/start", headers=headers)
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert url.startswith(google_oauth.AUTH_URL + "?")
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def picked(client, headers) -> dict:
    state = start(client, headers)["state"]
    response = client.post(
        f"{GOOGLE}/calendars", headers=headers, json={"code": "code-1", "state": state}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_options_offer_google_sign_in_only_when_set_up(client) -> None:
    assert client.get(f"{SUBSCRIBERS}/options").json()["google_sign_in"] is True
    app.dependency_overrides[get_settings] = lambda: settings(google_oauth_client_id="")
    assert client.get(f"{SUBSCRIBERS}/options").json()["google_sign_in"] is False
    response = client.post(f"{GOOGLE}/start", headers=auth(client))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "google_sign_in_not_offered"


def test_sign_in_asks_only_to_see_calendars_and_share_one(client) -> None:
    query = start(client, auth(client))
    assert query["client_id"] == "client-1.apps.googleusercontent.com"
    assert query["redirect_uri"] == "https://site.example/get-started"
    assert query["scope"].split() == list(google_oauth.SCOPES)
    assert query["access_type"] == "online"
    assert query["login_hint"] == "sam@example.com"
    assert query["response_type"] == "code"


def test_the_owner_picks_a_calendar_and_it_is_shared_then_connected(
    client, configured, checks
) -> None:
    headers = auth(client)
    body = picked(client, headers)
    # Their own calendars, the main one first, nothing deleted.
    assert body["calendars"] == [
        {"id": "sam@gmail.com", "name": "Sam Lee", "primary": True},
        {"id": "team@group.calendar.google.com", "name": "Team", "primary": False},
    ]
    # The ticket never shows Google's token.
    assert "user-token-1" not in body["ticket"]
    token_request = configured.requests[0]
    sent = dict(urllib.parse.parse_qsl(token_request[2].decode()))
    assert sent["code"] == "code-1" and sent["redirect_uri"] == "https://site.example/get-started"
    assert "minAccessRole=owner" in configured.requests[1][1]

    response = client.post(
        f"{GOOGLE}/connect",
        headers=headers,
        json={"ticket": body["ticket"], "calendar_id": "team@group.calendar.google.com"},
    )
    assert response.status_code == 200, response.text
    details = response.json()["details"]
    assert details["google_calendar_id"] == "team@group.calendar.google.com"
    assert details["google_calendar_connected"] is True
    method, url, acl, request_headers = configured.requests[-1]
    assert method == "POST"
    assert url == f"{API}/calendars/team%40group.calendar.google.com/acl?sendNotifications=false"
    assert json.loads(acl) == {"role": "writer", "scope": {"type": "user", "value": EMAIL}}
    assert request_headers["Authorization"] == "Bearer user-token-1"


def test_new_sharing_gets_a_moment_to_take_effect(client, checks) -> None:
    headers = auth(client)
    ticket = picked(client, headers)["ticket"]
    not_yet = CalendarAccessProblem("calendar_not_shared", "not yet")
    checks.extend([not_yet, not_yet])
    response = client.post(
        f"{GOOGLE}/connect",
        headers=headers,
        json={"ticket": ticket, "calendar_id": "sam@gmail.com"},
    )
    assert response.status_code == 200
    checks.extend([not_yet, not_yet, not_yet])
    response = client.post(
        f"{GOOGLE}/connect",
        headers=headers,
        json={"ticket": ticket, "calendar_id": "sam@gmail.com"},
    )
    assert response.json()["error"]["code"] == "calendar_not_shared"


def test_a_sign_in_belongs_to_the_account_that_started_it(client, checks) -> None:
    mine = auth(client)
    theirs = auth(client, email="other@example.com")
    state = start(client, mine)["state"]
    response = client.post(
        f"{GOOGLE}/calendars", headers=theirs, json={"code": "c", "state": state}
    )
    assert response.json()["error"]["code"] == "google_sign_in_expired"
    ticket = picked(client, mine)["ticket"]
    response = client.post(
        f"{GOOGLE}/connect", headers=theirs, json={"ticket": ticket, "calendar_id": "sam@gmail.com"}
    )
    assert response.json()["error"]["code"] == "google_sign_in_expired"
    # A state isn't a ticket.
    response = client.post(
        f"{GOOGLE}/connect", headers=mine, json={"ticket": state, "calendar_id": "sam@gmail.com"}
    )
    assert response.json()["error"]["code"] == "google_sign_in_expired"


def test_a_sign_in_expires_after_10_minutes(client, monkeypatch) -> None:
    headers = auth(client)
    state = start(client, headers)["state"]
    later = google_oauth.clock() + google_oauth.TTL_SECONDS + 5
    monkeypatch.setattr(google_oauth, "clock", lambda: later)
    response = client.post(
        f"{GOOGLE}/calendars", headers=headers, json={"code": "c", "state": state}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "google_sign_in_expired"


def test_both_permissions_are_needed(client, configured) -> None:
    configured.token_reply = (200, {"access_token": "t", "scope": google_oauth.SCOPES[0]})
    headers = auth(client)
    state = start(client, headers)["state"]
    response = client.post(
        f"{GOOGLE}/calendars", headers=headers, json={"code": "c", "state": state}
    )
    assert response.json()["error"]["code"] == "google_permission_missing"


def test_a_refused_code_asks_to_connect_again(client, configured) -> None:
    configured.token_reply = (400, None)
    headers = auth(client)
    state = start(client, headers)["state"]
    response = client.post(
        f"{GOOGLE}/calendars", headers=headers, json={"code": "c", "state": state}
    )
    assert response.json()["error"]["code"] == "google_sign_in_failed"


def test_a_workspace_that_blocks_sharing_is_explained_and_nothing_saved(
    client, configured, checks
) -> None:
    configured.acl_status = 403
    headers = auth(client)
    ticket = picked(client, headers)["ticket"]
    response = client.post(
        f"{GOOGLE}/connect",
        headers=headers,
        json={"ticket": ticket, "calendar_id": "sam@gmail.com"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "calendar_sharing_blocked"
    details = client.get(f"{SUBSCRIBERS}/profile", headers=headers).json()["details"]
    assert details["google_calendar_connected"] is False


def test_nothing_about_google_is_kept(client, db: Session, checks) -> None:
    headers = auth(client)
    ticket = picked(client, headers)["ticket"]
    client.post(
        f"{GOOGLE}/connect",
        headers=headers,
        json={"ticket": ticket, "calendar_id": "sam@gmail.com"},
    )
    stored = client.get(f"{SUBSCRIBERS}/profile", headers=headers).json()
    assert "user-token-1" not in json.dumps(stored)
