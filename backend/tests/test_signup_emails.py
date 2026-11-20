"""A submitted sign-up is emailed to the service's inbox, and the owner gets a receipt."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import WebhookEvent
from app.owner_email import send_due
from app.webhooks import deliver_due
from tests.test_owner_email import Outbox
from tests.test_subscribers import API, NOW, auth, complete, fixed_clock  # noqa: F401 - fixture

ADMIN = "takwa@helloeverai.example"


def settings(**overrides) -> Settings:
    values = {
        "resend_api_key": "re_test",
        "email_from": "notices@helloeverai.example",
        "admin_email": ADMIN,
        **overrides,
    }
    return Settings(**values)


def submit(client: TestClient) -> dict:
    headers = auth(client)
    complete(client, headers)
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    return headers


def notices(db: Session) -> list[WebhookEvent]:
    db.expire_all()
    return list(db.scalars(select(WebhookEvent).where(WebhookEvent.business_id.is_(None))))


def test_a_sign_up_emails_us_and_the_owner(client, db) -> None:
    submit(client)
    outbox = Outbox()
    assert send_due(db, settings(), NOW, outbox) == 2
    ours = next(m for m in outbox.sent if m["To"] == ADMIN)
    theirs = next(m for m in outbox.sent if m["To"] == "sam@example.com")

    assert ours["Subject"] == "New sign-up: Shaggy Chic (Pet grooming)"
    body = ours.get_content()
    assert "Sam Rivera submitted a profile for Shaggy Chic." in body
    assert "Phone: (415) 555-0134" in body
    assert "Calendar: Google Calendar" in body
    assert "/opt/venv/bin/python -m app.provision draft sam@example.com" in body
    assert "Web pages to learn from: -" in body

    assert theirs["Subject"] == "We received your profile for Shaggy Chic"
    assert theirs["From"] == "HelloEver AI <notices@helloeverai.example>"
    assert theirs["Reply-To"] == ADMIN
    assert "we'll contact you within one business day" in theirs.get_content()


def test_submitting_again_sends_nothing_new(client, db) -> None:
    headers = submit(client)
    client.post(f"{API}/profile/submit", headers=headers)
    assert sorted(e.event_type for e in notices(db)) == ["profile.received", "profile.submitted"]


def test_without_an_admin_inbox_the_owner_still_gets_a_receipt(client, db) -> None:
    submit(client)
    outbox = Outbox()
    assert send_due(db, settings(admin_email=""), NOW, outbox) == 1
    assert outbox.sent[0]["To"] == "sam@example.com"
    skipped = next(e for e in notices(db) if e.event_type == "profile.submitted")
    assert skipped.email_error == "no address to notify"


def test_sign_ups_never_go_to_a_business_webhook(client, db) -> None:
    submit(client)
    sent = []
    report = deliver_due(
        db,
        Settings(webhook_url="https://hooks.example/x", webhook_secret="s" * 40),
        NOW + timedelta(seconds=5),
        lambda *args: sent.append(args),
    )
    assert sent == [] and report.delivered == 0
