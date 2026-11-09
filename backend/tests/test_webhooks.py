"""Automation webhook events: written with the change they describe, delivered signed."""

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import WebhookEvent
from app.webhooks import MAX_ATTEMPTS, Attempt, configuration_problem, deliver_due
from tests.test_booking_api import NOW, confirm, prepare
from tests.test_staff_api import staff, token_for  # noqa: F401 - fixture

SECRET = "s" * 40
URL = "https://automation.example.com/webhook/bookings"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


def configured(**overrides) -> Settings:
    return Settings(
        webhook_url=URL, webhook_secret=SECRET, webhook_worker_enabled=False, **overrides
    )


class Receiver:
    """Stands in for the n8n Webhook node and records what it was sent."""

    def __init__(self, statuses=(200,)):
        self.statuses = list(statuses)
        self.requests: list[tuple[str, bytes, dict]] = []

    def __call__(self, url: str, body: bytes, headers: dict) -> Attempt:
        self.requests.append((url, body, headers))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return Attempt(status=status, error=None if status < 300 else f"HTTP {status}")


def events(db: Session) -> list[WebhookEvent]:
    db.expire_all()
    return db.scalars(select(WebhookEvent).order_by(WebhookEvent.created_at)).all()


def book(client: TestClient) -> dict:
    prepared = prepare(client).json()
    response = confirm(client, prepared)
    assert response.status_code == 201
    return response.json()


def test_a_confirmed_booking_records_one_event_with_its_details(client, db) -> None:
    booking = book(client)
    [event] = events(db)
    assert event.event_type == "booking.confirmed"
    data = event.payload["data"]
    assert data["reference"] == booking["reference"]
    assert data["service"] == "Full Groom"
    assert data["pet_name"] == "Biscuit"
    assert data["owner_email"] == "jordan@example.com"
    assert data["source"] == "form"
    assert data["local_start"].endswith("-08:00")
    assert event.payload["business"]["slug"] == "pawcare-demo"


def test_replaying_a_confirmation_does_not_send_a_second_event(client, db) -> None:
    prepared = prepare(client).json()
    confirm(client, prepared)
    assert confirm(client, prepared).status_code == 200
    assert len(events(db)) == 1


def test_a_failed_confirmation_records_no_event(client, db) -> None:
    prepared = prepare(client).json()
    bad = confirm(client, prepared, summary_hash="0" * 64)
    assert bad.status_code == 409
    assert events(db) == []


def test_cancelling_records_an_event_once(client, db, staff) -> None:  # noqa: F811
    booking = book(client)
    headers = token_for(client, staff)
    url = f"/api/v1/staff/bookings/{booking['reference']}/cancel"
    assert client.post(url, headers=headers).status_code == 200
    assert client.post(url, headers=headers).status_code == 200
    kinds = [e.event_type for e in events(db)]
    assert kinds == ["booking.confirmed", "booking.cancelled"]
    assert events(db)[1].payload["data"]["status"] == "cancelled"


def test_a_voice_callback_request_records_an_event(client, db) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(voice_mode="test")
    voice = client.post("/api/v1/voice/sessions").json()
    args = {"name": "Sam", "phone": "(323) 555-0101", "reason": "health", "note": "Itchy skin"}
    response = client.post(
        f"/api/v1/voice/sessions/{voice['session_id']}/tools/create_callback_request",
        json={"session_token": voice["session_token"], "arguments": json.dumps(args)},
    )
    assert response.json()["ok"] is True
    [event] = events(db)
    assert event.event_type == "callback.requested"
    assert event.payload["data"]["phone"] == "(323) 555-0101"
    assert event.payload["data"]["reason"] == "health"


def test_delivery_is_signed_so_the_receiver_can_verify_it(client, db) -> None:
    book(client)
    receiver = Receiver()
    report = deliver_due(db, configured(), NOW, send=receiver)
    assert report.delivered == 1
    [(url, body, headers)] = receiver.requests
    assert url == URL
    assert headers["X-Webhook-Event"] == "booking.confirmed"
    # What an n8n Code or Crypto node does with the secret.
    signed = f"{headers['X-Webhook-Timestamp']}.".encode() + body
    expected = "sha256=" + hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["X-Webhook-Signature"], expected)
    assert json.loads(body)["id"] == headers["X-Webhook-Id"]
    assert events(db)[0].delivered_at is not None


def test_a_delivered_event_is_not_sent_again(client, db) -> None:
    book(client)
    receiver = Receiver()
    deliver_due(db, configured(), NOW, send=receiver)
    deliver_due(db, configured(), NOW + timedelta(minutes=5), send=receiver)
    assert len(receiver.requests) == 1


def test_failures_are_retried_later_with_the_same_id(client, db) -> None:
    book(client)
    receiver = Receiver(statuses=(503, 200))
    first = deliver_due(db, configured(), NOW, send=receiver)
    assert first.retried == 1
    [event] = events(db)
    assert event.last_error == "HTTP 503"
    assert event.next_attempt_at == NOW + timedelta(seconds=30)
    # Not due yet, so nothing is sent.
    deliver_due(db, configured(), NOW + timedelta(seconds=10), send=receiver)
    assert len(receiver.requests) == 1
    deliver_due(db, configured(), NOW + timedelta(seconds=31), send=receiver)
    assert len(receiver.requests) == 2
    assert receiver.requests[0][2]["X-Webhook-Id"] == receiver.requests[1][2]["X-Webhook-Id"]
    assert events(db)[0].delivered_at is not None


def test_an_event_that_keeps_failing_is_given_up_after_the_last_attempt(client, db) -> None:
    book(client)
    receiver = Receiver(statuses=(500,))
    when = NOW
    for _ in range(MAX_ATTEMPTS):
        deliver_due(db, configured(), when, send=receiver)
        when += timedelta(hours=1)
    [event] = events(db)
    assert event.attempts == MAX_ATTEMPTS
    assert event.failed_at is not None
    deliver_due(db, configured(), when, send=receiver)
    assert len(receiver.requests) == MAX_ATTEMPTS


def test_without_a_webhook_events_wait_and_go_stale_rather_than_arrive_late(client, db) -> None:
    book(client)
    receiver = Receiver()
    deliver_due(db, Settings(webhook_worker_enabled=False), NOW, send=receiver)
    assert receiver.requests == []
    later = NOW + timedelta(hours=25)
    deliver_due(db, configured(), later, send=receiver)
    assert receiver.requests == []
    assert events(db)[0].last_error == "not delivered within 24 hours"


def test_old_events_are_deleted_because_they_hold_contact_details(client, db) -> None:
    book(client)
    deliver_due(db, configured(), NOW, send=Receiver())
    report = deliver_due(db, configured(), NOW + timedelta(days=31), send=Receiver())
    assert report.removed == 1
    assert events(db) == []


@pytest.mark.parametrize(
    ("url", "secret", "problem"),
    [
        ("", SECRET, "no webhook URL"),
        ("http://automation.example.com/hook", SECRET, "https"),
        ("ftp://automation.example.com/hook", SECRET, "web address"),
        (URL, "short", "at least 32"),
        ("http://localhost:5678/webhook/test", SECRET, None),
        (URL, SECRET, None),
    ],
)
def test_the_webhook_must_be_https_with_a_long_secret(url, secret, problem) -> None:
    found = configuration_problem(Settings(webhook_url=url, webhook_secret=secret))
    assert (found is None) if problem is None else (problem in found)


def test_staff_can_check_status_and_queue_a_test_event_without_seeing_secrets(
    client,
    db,
    staff,  # noqa: F811
) -> None:
    settings = configured()
    app.dependency_overrides[get_settings] = lambda: settings
    headers = token_for(client, staff)
    status = client.get("/api/v1/staff/webhooks", headers=headers)
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert SECRET not in status.text and URL not in status.text

    queued = client.post("/api/v1/staff/webhooks/test", headers=headers)
    assert queued.status_code == 202
    [event] = events(db)
    assert event.event_type == "webhook.test"
    assert str(event.id) == queued.json()["event_id"]
    assert client.get("/api/v1/staff/webhooks", headers=headers).json()["pending"] == 1

    deliver_due(db, configured(), NOW, send=Receiver())
    after = client.get("/api/v1/staff/webhooks", headers=headers).json()
    assert after["delivered_last_7_days"] == 1
    assert after["pending"] == 0


def test_webhook_endpoints_need_staff_sign_in(client) -> None:
    assert client.get("/api/v1/staff/webhooks").status_code == 401
    assert client.post("/api/v1/staff/webhooks/test").status_code == 401
