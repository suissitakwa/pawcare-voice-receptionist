"""Owner emails about bookings and callback requests, sent from the event outbox."""

import json
import smtplib
from collections.abc import Iterator
from datetime import timedelta
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import owner_email
from app.businesses import TEMPLATE_FILE, problems
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, WebhookEvent
from app.owner_email import MAX_ATTEMPTS, send_due, send_smtp
from app.schemas import BusinessSeed
from tests.test_booking_api import NOW, confirm, prepare
from tests.test_staff_api import staff, token_for  # noqa: F401 - fixture

OWNER = "owner@studio.example"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


def settings(**overrides) -> Settings:
    values = {
        "smtp_host": "smtp.example.com",
        "email_from": "Receptionist <bookings@example.com>",
        "public_site_url": "https://site.example/",
        **overrides,
    }
    return Settings(**values)


class Outbox:
    """Stands in for the mail server and keeps what it was given."""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.sent: list[EmailMessage] = []

    def __call__(self, _settings: Settings, message: EmailMessage) -> None:
        if self.failures:
            self.failures -= 1
            raise smtplib.SMTPServerDisconnected("connection closed")
        self.sent.append(message)


def notify(db: Session, address: str | None = OWNER) -> None:
    db.execute(update(Business).where(Business.slug == "pawcare-demo").values(notify_email=address))
    db.commit()


def book(client: TestClient) -> str:
    response = confirm(client, prepare(client).json())
    assert response.status_code == 201
    return response.json()["reference"]


def event(db: Session) -> WebhookEvent:
    db.expire_all()
    return db.scalar(select(WebhookEvent).order_by(WebhookEvent.created_at.desc()))


def test_a_new_booking_is_emailed_to_the_owner(client, db) -> None:
    notify(db)
    reference = book(client)
    outbox = Outbox()
    assert send_due(db, settings(), NOW, outbox) == 1
    [message] = outbox.sent
    assert message["To"] == OWNER
    assert message["From"] == "Receptionist <bookings@example.com>"
    assert message["Subject"] == "New booking: Full Groom for Biscuit, Tuesday, January 5 at 9 AM"
    body = message.get_content()
    assert "Owner: Jordan Example, jordan@example.com" in body
    assert f"Reference: {reference}" in body
    assert "Price: $90.00" in body
    assert "Staff dashboard: https://site.example/b/pawcare-demo/staff" in body
    # Sent once only.
    assert send_due(db, settings(), NOW, outbox) == 0
    assert event(db).email_sent_at == NOW


def test_a_callback_request_is_emailed(db) -> None:
    from app.models import CallbackRequest
    from app.webhooks import callback_data, enqueue

    notify(db)
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    callback = CallbackRequest(
        business_id=business_id,
        name="Riley",
        phone="+14155550100",
        email=None,
        reason="health",
        note="Itchy skin after last groom",
    )
    db.add(callback)
    db.flush()
    enqueue(db, business_id, "callback.requested", callback_data(callback), NOW)
    db.commit()
    outbox = Outbox()
    send_due(db, settings(), NOW, outbox)
    [message] = outbox.sent
    assert message["Subject"] == "Please call back: Riley (health question)"
    assert "Phone: +14155550100" in message.get_content()
    assert "Note: Itchy skin after last groom" in message.get_content()


def test_the_demo_never_emails_its_made_up_address(client, db) -> None:
    book(client)
    outbox = Outbox()
    assert send_due(db, settings(), NOW, outbox) == 0
    assert outbox.sent == []
    assert event(db).email_error == "no address to notify"


def test_nothing_is_sent_or_given_up_without_a_mail_server(client, db) -> None:
    notify(db)
    book(client)
    assert send_due(db, Settings(), NOW, Outbox()) == 0
    assert event(db).email_failed_at is None
    # Once a mail server is set up, the waiting email goes out.
    outbox = Outbox()
    assert send_due(db, settings(), NOW + timedelta(minutes=5), outbox) == 1


def test_a_failed_send_is_retried_then_given_up(client, db) -> None:
    notify(db)
    book(client)
    outbox = Outbox(failures=1)
    assert send_due(db, settings(), NOW, outbox) == 0
    assert event(db).email_error == "connection closed"
    # Not retried before the delay.
    assert send_due(db, settings(), NOW + timedelta(seconds=30), outbox) == 0
    assert send_due(db, settings(), NOW + timedelta(minutes=1), outbox) == 1

    book_again = event(db)
    book_again.email_sent_at = None
    db.commit()
    stuck = Outbox(failures=100)
    for minutes in range(0, 600, 70):
        send_due(db, settings(), NOW + timedelta(minutes=minutes), stuck)
    assert event(db).email_attempts == MAX_ATTEMPTS
    assert event(db).email_failed_at is not None


def test_an_old_notice_is_not_sent(client, db) -> None:
    notify(db)
    book(client)
    outbox = Outbox()
    assert send_due(db, settings(), NOW + timedelta(hours=25), outbox) == 0
    assert event(db).email_error == "not sent within 24 hours"


def test_smtp_uses_starttls_and_signs_in(monkeypatch) -> None:
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, message):
            calls.append(("send", message["To"]))

    monkeypatch.setattr(owner_email.smtplib, "SMTP", FakeSMTP)
    message = EmailMessage()
    message["To"] = OWNER
    send_smtp(settings(smtp_username="user", smtp_password="app-password"), message)
    assert calls == [
        ("connect", "smtp.example.com", 587),
        ("starttls",),
        ("login", "user", "app-password"),
        ("send", OWNER),
    ]


def test_staff_see_where_emails_go(client, db, staff) -> None:  # noqa: F811
    notify(db)
    app.dependency_overrides[get_settings] = lambda: settings()
    book(client)
    send_due(db, settings(), NOW, Outbox())
    status = client.get("/api/v1/staff/emails", headers=token_for(client, staff)).json()
    assert status == {
        "address": OWNER,
        "enabled": True,
        "sent_last_7_days": 1,
        "last_error": None,
    }


def test_business_files_check_the_notify_address() -> None:
    data = json.loads(TEMPLATE_FILE.read_text())
    seed = BusinessSeed.model_validate({**data, "notify_email": "not-an-email"})
    assert "notify_email 'not-an-email' is not an email address" in problems(seed)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_resend_is_used_when_it_has_a_key(client, db, monkeypatch) -> None:
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return FakeResponse()

    monkeypatch.setattr(owner_email.urllib.request, "urlopen", fake_urlopen)
    notify(db)
    book(client)
    resend = Settings(resend_api_key="re_test", email_from="Pilot <onboarding@resend.dev>")
    assert owner_email.email_problem(resend) is None
    assert send_due(db, resend, NOW) == 1
    [request] = sent
    assert request.full_url == "https://api.resend.com/emails"
    assert request.get_header("Authorization") == "Bearer re_test"
    assert request.get_header("Idempotency-key")
    body = json.loads(request.data)
    assert body["to"] == [OWNER]
    assert body["from"] == "Pilot <onboarding@resend.dev>"
    assert body["subject"].startswith("New booking: Full Groom for Biscuit")
    assert "Reference: " in body["text"]


def test_a_resend_refusal_is_recorded_and_retried(client, db, monkeypatch) -> None:
    import io
    import urllib.error

    def refuse(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"domain not verified"}')
        )

    monkeypatch.setattr(owner_email.urllib.request, "urlopen", refuse)
    notify(db)
    book(client)
    resend = Settings(resend_api_key="re_test", email_from="Pilot <bookings@example.com>")
    assert send_due(db, resend, NOW) == 0
    stored = event(db)
    assert stored.email_error.startswith("Resend answered HTTP 403")
    assert "domain not verified" in stored.email_error
    assert stored.email_next_attempt_at is not None and stored.email_failed_at is None


def test_an_appointment_request_says_so(db) -> None:
    from app.models import CallbackRequest
    from app.webhooks import callback_data, enqueue

    notify(db)
    business_id = db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))
    callback = CallbackRequest(
        business_id=business_id,
        name="Takwa",
        phone="+16693331241",
        email=None,
        reason="booking_help",
        note="Wants a Full Groom for Mila, a large dog.",
    )
    db.add(callback)
    db.flush()
    enqueue(db, business_id, "callback.requested", callback_data(callback), NOW)
    db.commit()
    outbox = Outbox()
    send_due(db, settings(), NOW, outbox)
    [message] = outbox.sent
    assert message["Subject"] == "Please call back: Takwa (wants an appointment)"
    assert "Reason: wants an appointment" in message.get_content()
