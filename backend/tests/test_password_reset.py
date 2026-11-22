"""Forgotten passwords: a one-time emailed link for sign-up and staff accounts."""

import re
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import WebhookEvent
from app.owner_email import send_due
from tests.test_owner_email import Outbox
from tests.test_staff_api import PASSWORD as STAFF_PASSWORD
from tests.test_staff_api import staff  # noqa: F401 - fixture
from tests.test_subscribers import API, NOW, PASSWORD, sign_up

SITE = "https://app.helloever.example"
NEW = "a-brand-new-password-42"


def settings(**overrides) -> Settings:
    values = {
        "public_site_url": SITE,
        "resend_api_key": "re_test",
        "email_from": "notices@helloever.example",
        **overrides,
    }
    return Settings(**values)


@pytest.fixture(autouse=True)
def configured() -> Iterator[dict]:
    state = {"settings": settings(), "now": NOW}
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_now] = lambda: state["now"]
    yield state
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_now, None)


def resets(db: Session) -> list[WebhookEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(WebhookEvent)
            .where(WebhookEvent.event_type == "password.reset")
            .order_by(WebhookEvent.created_at)
        )
    )


def link_token(event: WebhookEvent) -> str:
    return re.search(r"reset=([\w-]+)", event.payload["data"]["link"]).group(1)


def forgot(client: TestClient, email: str = "sam@example.com", ip: str = "198.51.100.7"):
    return client.post(
        f"{API}/password/forgot", json={"email": email}, headers={"X-Forwarded-For": ip}
    )


def sign_in(client: TestClient, password: str):
    return client.post(f"{API}/login", json={"email": "sam@example.com", "password": password})


# Sign-up accounts


def test_a_forgotten_password_is_reset_from_the_emailed_link(client, db) -> None:
    old_session = sign_up(client).json()["token"]
    assert forgot(client).status_code == 202
    [event] = resets(db)
    assert event.payload["data"]["link"].startswith(f"{SITE}/get-started?reset=")
    token = link_token(event)

    outbox = Outbox()
    assert send_due(db, settings(), NOW, outbox) == 1
    [message] = outbox.sent
    assert message["To"] == "sam@example.com"
    assert message["Subject"] == "Reset your HelloEver AI password"
    assert f"{SITE}/get-started?reset={token}" in message.get_content()
    # The link is erased from the outbox once emailed.
    assert token not in str(resets(db)[0].payload)

    reset = client.post(f"{API}/password/reset", json={"token": token, "password": NEW})
    assert reset.status_code == 204
    assert sign_in(client, PASSWORD).status_code == 401
    assert sign_in(client, NEW).status_code == 200
    # Every other session was signed out.
    headers = {"Authorization": f"Bearer {old_session}"}
    assert client.get(f"{API}/me", headers=headers).status_code == 401
    # And they're told.
    changed = Outbox()
    send_due(db, settings(), NOW, changed)
    assert [m["Subject"] for m in changed.sent] == ["Your HelloEver AI password was changed"]


def test_a_link_works_once_and_for_an_hour(client, db, configured) -> None:
    sign_up(client)
    forgot(client)
    token = link_token(resets(db)[0])
    configured["now"] = NOW + timedelta(minutes=61)
    expired = client.post(f"{API}/password/reset", json={"token": token, "password": NEW})
    assert expired.json()["error"]["code"] == "reset_link_invalid"

    configured["now"] = NOW + timedelta(minutes=62)
    forgot(client)
    token = link_token(resets(db)[-1])
    assert (
        client.post(f"{API}/password/reset", json={"token": token, "password": NEW}).status_code
        == 204
    )
    again = client.post(f"{API}/password/reset", json={"token": token, "password": NEW + "x"})
    assert again.json()["error"]["code"] == "reset_link_invalid"


def test_only_the_newest_link_works(client, db, configured) -> None:
    sign_up(client)
    forgot(client)
    # A second later, so "newest" is never a tie between the two links.
    configured["now"] = NOW + timedelta(seconds=1)
    forgot(client)
    first, second = (link_token(e) for e in resets(db))
    assert (
        client.post(f"{API}/password/reset", json={"token": first, "password": NEW}).status_code
        == 400
    )
    assert (
        client.post(f"{API}/password/reset", json={"token": second, "password": NEW}).status_code
        == 204
    )


def test_an_unknown_email_gets_the_same_answer_and_no_email(client, db) -> None:
    answer = forgot(client, "nobody@example.com")
    assert answer.status_code == 202
    assert answer.json() == {"status": "sent_if_account_exists"}
    assert resets(db) == []


def test_requests_are_limited(client, db) -> None:
    sign_up(client)
    for _ in range(5):
        assert forgot(client).status_code == 202
    assert len(resets(db)) == 3


def test_short_passwords_are_refused(client, db) -> None:
    sign_up(client)
    forgot(client)
    token = link_token(resets(db)[0])
    short = client.post(f"{API}/password/reset", json={"token": token, "password": "short"})
    assert short.status_code == 422


def test_without_a_site_address_no_link_is_made(client, db, configured) -> None:
    configured["settings"] = settings(public_site_url="")
    sign_up(client)
    assert forgot(client).status_code == 202
    assert resets(db) == []


# Staff dashboard logins


def test_staff_reset_their_password_at_their_business(client, db, staff, configured) -> None:  # noqa: F811
    asked = client.post(
        "/api/v1/b/pawcare-demo/staff/password/forgot", json={"email": "maya@pawcare.example"}
    )
    assert asked.status_code == 202
    [event] = resets(db)
    assert event.payload["data"]["link"].startswith(f"{SITE}/b/pawcare-demo/staff?reset=")
    token = link_token(event)
    # A staff link can't be used on the sign-up reset, or the other way round.
    assert (
        client.post(f"{API}/password/reset", json={"token": token, "password": NEW}).status_code
        == 400
    )
    configured["now"] = NOW + timedelta(seconds=1)
    forgot_again = client.post(
        "/api/v1/b/pawcare-demo/staff/password/forgot", json={"email": "maya@pawcare.example"}
    )
    assert forgot_again.status_code == 202
    token = link_token(resets(db)[-1])
    done = client.post("/api/v1/staff/password/reset", json={"token": token, "password": NEW})
    assert done.status_code == 204
    login = "/api/v1/b/pawcare-demo/staff/login"
    assert (
        client.post(
            login, json={"email": "maya@pawcare.example", "password": STAFF_PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(login, json={"email": "maya@pawcare.example", "password": NEW}).status_code
        == 200
    )


def test_an_unknown_business_gets_the_same_answer(client, db) -> None:
    answer = client.post("/api/v1/b/nowhere/staff/password/forgot", json={"email": "a@b.example"})
    assert answer.status_code == 202
    assert resets(db) == []
