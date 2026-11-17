"""Each business's calendar and webhook are its own, and secrets are stored encrypted."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import google_calendar
from app.config import Settings, get_settings
from app.google_calendar import Busy, sync_round
from app.integrations import (
    CALENDAR,
    WEBHOOK,
    IntegrationUnavailable,
    calendar_config,
    remove,
    set_calendar,
    set_webhook,
    webhook_config,
)
from app.main import app, get_now
from app.models import Booking, Business, BusinessIntegration, WebhookEvent
from app.webhooks import Attempt, business_problem, deliver_due, enqueue, sign
from tests.test_google_calendar import FakeCalendar

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
KEY = Fernet.generate_key().decode()
DEMO_URL = "https://automation.example.com/demo"
SECOND_URL = "https://hooks.example.org/second"
DEMO_SECRET = "d" * 40
SECOND_SECRET = "s" * 40
SERVICE_ACCOUNT = json.dumps({"client_email": "bot@example.iam", "private_key": "not-checked"})


def settings_with(**overrides) -> Settings:
    values = {"integrations_key": KEY, "webhook_worker_enabled": False}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def clean(db: Session) -> Iterator[None]:
    db.execute(delete(BusinessIntegration))
    db.commit()
    app.dependency_overrides[get_now] = lambda: NOW
    google_calendar.clear_busy_cache()
    yield
    db.rollback()
    db.execute(delete(BusinessIntegration))
    db.commit()
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)
    google_calendar.clear_busy_cache()


def business_id(db: Session, slug: str):
    return db.scalar(select(Business.id).where(Business.slug == slug))


# Storing settings


def test_secrets_are_stored_encrypted_and_read_back(db, second_business) -> None:
    settings = settings_with()
    set_webhook(db, settings, second_business, SECOND_URL, SECOND_SECRET)
    db.commit()
    stored = db.scalar(select(BusinessIntegration.secret))
    assert SECOND_SECRET.encode() not in stored
    found = webhook_config(db, settings, business_id(db, second_business))
    assert (found.url, found.secret) == (SECOND_URL, SECOND_SECRET)


def test_saving_needs_a_key_and_reading_needs_the_same_key(db, second_business) -> None:
    with pytest.raises(ValueError, match="INTEGRATIONS_KEY"):
        set_webhook(db, Settings(), second_business, SECOND_URL, SECOND_SECRET)
    set_webhook(db, settings_with(), second_business, SECOND_URL, SECOND_SECRET)
    db.commit()
    other_key = settings_with(integrations_key=Fernet.generate_key().decode())
    with pytest.raises(IntegrationUnavailable):
        webhook_config(db, other_key, business_id(db, second_business))


@pytest.mark.parametrize(
    "url, secret", [("http://example.com/hook", "x" * 40), (SECOND_URL, "short"), ("", "x" * 40)]
)
def test_unusable_webhooks_are_refused(db, second_business, url, secret) -> None:
    with pytest.raises(ValueError):
        set_webhook(db, settings_with(), second_business, url, secret)


def test_calendar_needs_a_service_account_key_and_a_known_business(db, second_business) -> None:
    with pytest.raises(ValueError):
        set_calendar(db, settings_with(), second_business, "cal@example.com", "{}")
    with pytest.raises(ValueError):
        set_calendar(db, settings_with(), "no-such", "cal@example.com", SERVICE_ACCOUNT)
    set_calendar(db, settings_with(), second_business, "cal@example.com", SERVICE_ACCOUNT)
    db.commit()
    assert remove(db, second_business, CALENDAR) is True
    assert remove(db, second_business, CALENDAR) is False


def test_environment_settings_serve_only_the_deployments_business(db, second_business) -> None:
    settings = settings_with(
        webhook_url=DEMO_URL,
        webhook_secret=DEMO_SECRET,
        google_calendar_id="demo@example.com",
        google_service_account_json=SERVICE_ACCOUNT,
    )
    demo, second = business_id(db, "pawcare-demo"), business_id(db, second_business)
    assert webhook_config(db, settings, demo).url == DEMO_URL
    assert calendar_config(db, settings, demo).calendar_id == "demo@example.com"
    assert webhook_config(db, settings, second) is None
    assert calendar_config(db, settings, second) is None

    # A stored setting wins over the environment, even for the deployment's business.
    set_webhook(db, settings, "pawcare-demo", SECOND_URL, SECOND_SECRET)
    db.commit()
    assert webhook_config(db, settings, demo).url == SECOND_URL


# Webhooks


class Receiver:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, body: bytes, headers: dict) -> Attempt:
        self.sent.append((url, json.loads(body), headers))
        return Attempt(status=200)


def test_each_business_events_go_to_its_own_webhook(db, second_business) -> None:
    settings = settings_with(webhook_url=DEMO_URL, webhook_secret=DEMO_SECRET)
    set_webhook(db, settings, second_business, SECOND_URL, SECOND_SECRET)
    enqueue(db, business_id(db, "pawcare-demo"), "test.ping", {"n": 1}, NOW)
    enqueue(db, business_id(db, second_business), "test.ping", {"n": 2}, NOW)
    db.commit()

    receiver = Receiver()
    assert deliver_due(db, settings, NOW, send=receiver).delivered == 2
    by_url = {url: (payload, headers) for url, payload, headers in receiver.sent}
    assert by_url[DEMO_URL][0]["business"]["slug"] == "pawcare-demo"
    assert by_url[SECOND_URL][0]["business"]["slug"] == second_business
    for url, secret in ((DEMO_URL, DEMO_SECRET), (SECOND_URL, SECOND_SECRET)):
        payload, headers = by_url[url]
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = int(headers["X-Webhook-Timestamp"])
        assert headers["X-Webhook-Signature"] == sign(secret, timestamp, body)


def test_a_business_without_a_webhook_keeps_its_events_and_blocks_no_one(
    db, second_business
) -> None:
    settings = settings_with(webhook_url=DEMO_URL, webhook_secret=DEMO_SECRET)
    second = business_id(db, second_business)
    for n in range(25):  # more than one delivery batch
        enqueue(db, second, "test.ping", {"n": n}, NOW - timedelta(minutes=1))
    enqueue(db, business_id(db, "pawcare-demo"), "test.ping", {"n": "demo"}, NOW)
    db.commit()

    receiver = Receiver()
    deliver_due(db, settings, NOW, send=receiver)
    assert [url for url, _, _ in receiver.sent] == [DEMO_URL]
    db.expire_all()
    waiting = db.scalars(select(WebhookEvent).where(WebhookEvent.business_id == second)).all()
    assert len(waiting) == 25 and all(event.delivered_at is None for event in waiting)
    assert business_problem(db, settings, second) == "no webhook URL is set"
    assert business_problem(db, settings, business_id(db, "pawcare-demo")) is None


# Calendars


def book(client: TestClient, slug: str) -> None:
    prepared = client.post(
        f"/api/v1/b/{slug}/bookings/prepare",
        json={
            "service": "bath-and-tidy",
            "size": "small",
            "starts_at": NINE.isoformat(),
            "owner_name": "Jordan Example",
            "owner_email": "jordan@example.com",
            "pet_name": "Biscuit",
        },
    ).json()
    response = client.post(
        f"/api/v1/b/{slug}/bookings/confirm",
        json={
            "confirmation_token": prepared["confirmation_token"],
            "summary_hash": prepared["summary_hash"],
        },
        headers={"Idempotency-Key": f"key-{slug}-0001"},
    )
    assert response.status_code == 201


@pytest.fixture
def calendars(monkeypatch) -> dict[str, FakeCalendar]:
    """Fake Google clients, one per calendar id, reached through each business's settings."""
    fakes = {"demo@example.com": FakeCalendar(), "second@example.com": FakeCalendar()}
    for calendar_id, fake in fakes.items():
        fake.calendar_id = calendar_id
    monkeypatch.setattr(google_calendar, "client_for", lambda config: fakes[config.calendar_id])
    return fakes


def use(settings: Settings, monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(google_calendar, "get_settings", lambda: settings)


def two_calendars(db, second_business) -> Settings:
    settings = settings_with(
        google_calendar_id="demo@example.com", google_service_account_json=SERVICE_ACCOUNT
    )
    set_calendar(db, settings, second_business, "second@example.com", SERVICE_ACCOUNT)
    db.commit()
    return settings


def test_each_business_bookings_sync_to_its_own_calendar(
    client, db, second_business, calendars, monkeypatch
) -> None:
    use(two_calendars(db, second_business), monkeypatch)
    book(client, "pawcare-demo")
    book(client, second_business)

    assert sync_round(db, get_settings_override(), NOW) == 2
    [demo_event] = calendars["demo@example.com"].saved.values()
    [second_event] = calendars["second@example.com"].saved.values()
    assert "PawCare Grooming Studio" in demo_event["description"]
    assert "Second Studio" in second_event["description"]


def test_bookings_of_a_business_without_a_calendar_are_left_alone(
    client, db, second_business, calendars, monkeypatch
) -> None:
    settings = settings_with(
        google_calendar_id="demo@example.com", google_service_account_json=SERVICE_ACCOUNT
    )
    use(settings, monkeypatch)
    book(client, second_business)
    assert sync_round(db, settings, NOW) == 0
    assert calendars["demo@example.com"].saved == {}
    db.expire_all()
    assert db.scalar(select(Booking.calendar_dirty)) is True


def test_busy_times_block_only_their_own_business(
    client, db, second_business, calendars, monkeypatch
) -> None:
    use(two_calendars(db, second_business), monkeypatch)
    calendars["demo@example.com"].busy_times = [Busy(NINE, NINE + timedelta(hours=1))]

    def nine_is_open(slug: str) -> bool:
        params = {"service": "bath-and-tidy", "size": "small", "date": "2027-01-05"}
        body = client.get(f"/api/v1/b/{slug}/availability", params=params).json()
        return any(slot["local_start"].startswith("2027-01-05T09:00") for slot in body["slots"])

    assert nine_is_open("pawcare-demo") is False
    assert nine_is_open(second_business) is True


def test_unreadable_calendar_settings_stop_booking_for_that_business_only(
    client, db, second_business, calendars, monkeypatch
) -> None:
    two_calendars(db, second_business)
    use(settings_with(integrations_key=Fernet.generate_key().decode()), monkeypatch)
    params = {"service": "bath-and-tidy", "size": "small", "date": "2027-01-05"}
    broken = client.get(f"/api/v1/b/{second_business}/availability", params=params)
    assert broken.status_code == 503
    assert broken.json()["error"]["code"] == "calendar_unavailable"
    assert client.get("/api/v1/b/pawcare-demo/availability", params=params).status_code == 200


def get_settings_override() -> Settings:
    return app.dependency_overrides[get_settings]()


def test_webhook_kind_constant_matches_the_database(db, second_business) -> None:
    set_webhook(db, settings_with(), second_business, SECOND_URL, SECOND_SECRET)
    db.commit()
    assert db.scalar(select(BusinessIntegration.kind)) == WEBHOOK
