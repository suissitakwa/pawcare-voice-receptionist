"""Texts to customers: confirmations, changes, cancellations and reminders."""

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import customer_texts
from app.config import Settings
from app.customer_texts import MAX_ATTEMPTS, Refused, Retry, Text, send_due, send_twilio
from app.main import app, get_now
from app.models import Booking, Business, WebhookEvent
from app.webhooks import booking_data, enqueue
from tests.factories import make_booking
from tests.test_booking_api import LA, NINE, NOW, confirm, prepare
from tests.test_staff_api import STAFF, staff, token_for  # noqa: F401 - fixture

SHOP = "+13235550142"
CUSTOMER = "+14155550100"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def texts_on(db: Session) -> Iterator[None]:
    switch(db, True)
    yield
    switch(db, False)


def switch(db: Session, on: bool) -> None:
    db.execute(update(Business).where(Business.slug == "pawcare-demo").values(texts_enabled=on))
    db.commit()


def settings(**overrides) -> Settings:
    values = {
        "twilio_account_sid": "AC00000000000000000000000000000000",
        "twilio_auth_token": "test-token",
        "twilio_messaging_service_sid": "",
        "phone_number": SHOP,
        "business_slug": "pawcare-demo",
        "reminder_hours_before": 24,
        **overrides,
    }
    return Settings(**values)


def event_settings(**overrides) -> Settings:
    """For tests about event texts that move the clock on: no reminder becomes due."""
    return settings(reminder_hours_before=1, **overrides)


class Phone:
    """Stands in for Twilio and keeps what it was given."""

    def __init__(self, *errors: Exception):
        self.errors = list(errors)
        self.sent: list[Text] = []

    def __call__(self, _settings: Settings, text: Text) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(text)


def book(client, **overrides) -> str:
    response = confirm(client, prepare(client, owner_phone="(415) 555-0100", **overrides).json())
    assert response.status_code == 201
    return response.json()["reference"]


def last_event(db: Session) -> WebhookEvent:
    db.expire_all()
    return db.scalar(select(WebhookEvent).order_by(WebhookEvent.created_at.desc()))


def booking_with_phone(db: Session, start: datetime = NINE, phone: str = CUSTOMER) -> Booking:
    booking = make_booking(db, start)
    booking.owner_phone = phone
    db.commit()
    return booking


# Confirmations, changes and cancellations


def test_a_new_booking_is_texted_to_the_customer(client, db, texts_on) -> None:
    reference = book(client)
    phone = Phone()
    assert send_due(db, settings(), NOW, phone) == 1
    [text] = phone.sent
    assert text.to == CUSTOMER
    assert text.from_number == SHOP
    assert text.messaging_service_sid is None
    assert text.body == (
        "PawCare Grooming Studio: you're booked. Full Groom for Biscuit, Tue, Jan 5 at 9 AM. "
        f"Ref {reference}. To change it, call (323) 555-0142. Reply STOP to opt out."
    )
    # Once only.
    assert send_due(db, settings(), NOW, phone) == 0
    assert last_event(db).text_sent_at == NOW


def test_a_cancellation_by_staff_is_texted(client, db, staff, texts_on) -> None:  # noqa: F811
    booking = booking_with_phone(db)
    response = client.post(
        f"{STAFF}/bookings/{booking.reference}/cancel", headers=token_for(client, staff)
    )
    assert response.status_code == 200
    phone = Phone()
    send_due(db, settings(), NOW, phone)
    [text] = phone.sent
    assert text.body.startswith("PawCare Grooming Studio: your booking is cancelled. ")
    assert "Tue, Jan 5 at 9 AM" in text.body
    assert "To book again, call (323) 555-0142." in text.body


def test_a_change_is_texted_with_the_new_time(db, texts_on) -> None:
    booking = booking_with_phone(db)
    business = db.get(Business, booking.business_id)
    booking.starts_at = booking.starts_at + timedelta(hours=2)
    booking.ends_at = booking.ends_at + timedelta(hours=2)
    booking.occupied_until = booking.occupied_until + timedelta(hours=2)
    enqueue(db, business.id, "booking.changed", booking_data(booking, business), NOW)
    db.commit()
    phone = Phone()
    send_due(db, settings(), NOW, phone)
    [text] = phone.sent
    assert "your booking is updated. " in text.body
    assert "Tue, Jan 5 at 11 AM" in text.body


def test_texts_name_the_staff_member_and_the_address(db, texts_on) -> None:
    business = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    data = {
        "reference": "PC-ABC",
        "service": "Haircut",
        "pet_name": "",
        "staff": "Sam",
        "service_address": "12 Oak St",
        "local_start": "2027-01-05T14:30:00-08:00",
    }
    body = customer_texts.compose("booking.confirmed", data, business)
    assert body.startswith("PawCare Grooming Studio: you're booked. Haircut with Sam, ")
    assert "Tue, Jan 5 at 2:30 PM at 12 Oak St." in body


def test_nothing_is_texted_while_texts_are_off(client, db) -> None:
    book(client)
    phone = Phone()
    assert send_due(db, settings(), NOW, phone) == 0
    assert phone.sent == []
    event = last_event(db)
    assert event.text_failed_at == NOW
    assert event.text_error == "texts are off for this business"


def test_a_booking_without_a_phone_number_is_not_texted(client, db, texts_on) -> None:
    confirm(client, prepare(client).json())
    phone = Phone()
    send_due(db, settings(), NOW, phone)
    assert phone.sent == []
    assert last_event(db).text_error == "no phone number that can be texted"


def test_a_business_with_no_number_to_text_from_sends_nothing(client, db, texts_on) -> None:
    book(client)
    phone = Phone()
    send_due(db, settings(phone_number=""), NOW, phone)
    assert phone.sent == []
    assert last_event(db).text_error == "the business has no number to text from"


def test_a_messaging_service_is_used_when_set(client, db, texts_on) -> None:
    book(client)
    phone = Phone()
    send_due(db, settings(twilio_messaging_service_sid="MG123"), NOW, phone)
    [text] = phone.sent
    assert text.messaging_service_sid == "MG123"
    assert text.from_number is None


def test_without_twilio_nothing_is_touched(client, db, texts_on) -> None:
    book(client)
    phone = Phone()
    assert send_due(db, settings(twilio_account_sid="", twilio_auth_token=""), NOW, phone) == 0
    event = last_event(db)
    assert event.text_failed_at is None
    assert event.text_attempts == 0


def test_a_failed_send_is_retried_then_given_up(client, db, texts_on) -> None:
    book(client)
    phone = Phone(*[Retry("Twilio answered HTTP 503")] * MAX_ATTEMPTS)
    now = NOW
    for _ in range(MAX_ATTEMPTS):
        send_due(db, event_settings(), now, phone)
        now += timedelta(hours=1)
    event = last_event(db)
    assert phone.sent == []
    assert event.text_attempts == MAX_ATTEMPTS
    assert event.text_failed_at is not None
    assert event.text_error == "Twilio answered HTTP 503"


def test_a_retry_waits_before_trying_again(client, db, texts_on) -> None:
    book(client)
    phone = Phone(Retry("Twilio answered HTTP 503"))
    send_due(db, event_settings(), NOW, phone)
    assert last_event(db).text_next_attempt_at == NOW + timedelta(minutes=1)
    send_due(db, event_settings(), NOW + timedelta(seconds=30), phone)
    assert phone.sent == []
    send_due(db, event_settings(), NOW + timedelta(minutes=1), phone)
    assert len(phone.sent) == 1


def test_a_refusal_is_final(client, db, texts_on) -> None:
    book(client)
    phone = Phone(Refused("Twilio 21610: Attempt to send to unsubscribed recipient"))
    send_due(db, event_settings(), NOW, phone)
    send_due(db, event_settings(), NOW + timedelta(hours=1), phone)
    event = last_event(db)
    assert phone.sent == []
    assert event.text_attempts == 1
    assert event.text_error.startswith("Twilio 21610")


def test_an_old_event_is_not_texted_late(client, db, texts_on) -> None:
    book(client)
    phone = Phone()
    send_due(db, event_settings(), NOW + timedelta(hours=7), phone)
    assert phone.sent == []
    assert last_event(db).text_error == "not sent in time"


def test_events_from_before_the_change_are_never_texted(db) -> None:
    # The migration marks them; new events start unmarked.
    business = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    event = enqueue(db, business.id, "booking.confirmed", {}, NOW)
    db.commit()
    assert event.text_failed_at is None


# Reminders


def test_a_reminder_goes_out_a_day_ahead_once(db, texts_on) -> None:
    booking = booking_with_phone(db)
    phone = Phone()
    # 25 hours ahead: not yet.
    assert send_due(db, settings(), NOW, phone) == 0
    due = NINE - timedelta(hours=24)
    assert send_due(db, settings(), due, phone) == 1
    [text] = phone.sent
    assert text.to == CUSTOMER
    # The factory's booking has no summary, so no service name.
    assert text.body.startswith("Reminder from PawCare Grooming Studio: Your appointment for Rex,")
    assert "Tue, Jan 5 at 9 AM" in text.body
    assert f"Ref {booking.reference}." in text.body
    assert send_due(db, settings(), due + timedelta(hours=1), phone) == 0
    db.refresh(booking)
    assert booking.reminder_for == booking.starts_at
    assert booking.reminder_error is None


def test_a_moved_booking_gets_a_new_reminder(db, texts_on) -> None:
    booking = booking_with_phone(db)
    phone = Phone()
    send_due(db, settings(), NINE - timedelta(hours=24), phone)
    booking.starts_at = datetime(2027, 1, 6, 9, 0, tzinfo=LA)
    booking.ends_at = booking.starts_at + timedelta(minutes=30)
    booking.occupied_until = booking.ends_at + timedelta(minutes=15)
    db.commit()
    send_due(db, settings(), booking.starts_at - timedelta(hours=24), phone)
    assert len(phone.sent) == 2
    assert "Wed, Jan 6 at 9 AM" in phone.sent[1].body


def test_a_booking_made_inside_the_window_gets_no_reminder(db, texts_on) -> None:
    booking = booking_with_phone(db)
    booking.created_at = NINE - timedelta(hours=10)
    db.commit()
    phone = Phone()
    send_due(db, settings(), NINE - timedelta(hours=9), phone)
    assert phone.sent == []
    db.refresh(booking)
    assert booking.reminder_for == booking.starts_at
    assert booking.reminder_error == "booked within the reminder window"


def test_cancelled_and_imminent_bookings_get_no_reminder(db, texts_on) -> None:
    cancelled = booking_with_phone(db)
    cancelled.status = "cancelled"
    db.commit()
    booking_with_phone(db, datetime(2027, 1, 5, 14, 0, tzinfo=LA))
    phone = Phone()
    # Under an hour before the 2 PM booking.
    send_due(db, settings(), datetime(2027, 1, 5, 13, 30, tzinfo=LA), phone)
    assert phone.sent == []


def test_no_reminders_while_texts_are_off(db) -> None:
    booking_with_phone(db)
    phone = Phone()
    send_due(db, settings(), NINE - timedelta(hours=24), phone)
    assert phone.sent == []


def test_a_reminder_that_fails_is_retried(db, texts_on) -> None:
    booking = booking_with_phone(db)
    phone = Phone(Retry("Twilio can't be reached: refused"))
    due = NINE - timedelta(hours=24)
    send_due(db, settings(), due, phone)
    db.refresh(booking)
    assert booking.reminder_for is None
    assert booking.reminder_next_attempt_at == due + timedelta(minutes=1)
    send_due(db, settings(), due + timedelta(minutes=1), phone)
    assert len(phone.sent) == 1


# The request to Twilio


class FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.twilio.com", code, "error", {}, io.BytesIO(json.dumps(body).encode())
    )


def test_twilio_gets_the_number_and_body(monkeypatch) -> None:
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["fields"] = urllib.parse.parse_qs(request.data.decode())
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse(b"{}")

    monkeypatch.setattr(customer_texts.urllib.request, "urlopen", fake_urlopen)
    send_twilio(settings(), Text(to=CUSTOMER, body="Hi", from_number=SHOP))
    assert seen["url"] == (
        "https://api.twilio.com/2010-04-01/Accounts/AC00000000000000000000000000000000/Messages.json"
    )
    assert seen["fields"] == {"To": [CUSTOMER], "Body": ["Hi"], "From": [SHOP]}
    assert seen["auth"].startswith("Basic ")


@pytest.mark.parametrize(
    ("error", "expected", "message"),
    [
        (http_error(400, {"code": 21610, "message": "unsubscribed"}), Refused, "Twilio 21610"),
        (http_error(503, {"code": 20503, "message": "down"}), Retry, "Twilio 20503"),
        (http_error(429, {"code": 20429, "message": "too many"}), Retry, "Twilio 20429"),
        (urllib.error.URLError(ConnectionRefusedError("refused")), Retry, "can't be reached"),
        (urllib.error.URLError(TimeoutError("timed out")), Refused, "may have been sent"),
        (TimeoutError("timed out"), Refused, "may have been sent"),
    ],
)
def test_twilio_errors_are_retried_only_when_nothing_was_sent(
    monkeypatch, error, expected, message
) -> None:
    def fake_urlopen(_request, timeout):
        raise error

    monkeypatch.setattr(customer_texts.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(expected, match=message):
        send_twilio(settings(), Text(to=CUSTOMER, body="Hi", from_number=SHOP))


# Switching texts on


def test_texts_are_switched_on_and_off_per_business(db, monkeypatch, capsys) -> None:
    from app import businesses

    def run(*args: str) -> None:
        monkeypatch.setattr(businesses.sys, "argv", ["businesses", *args])
        businesses.main()

    def enabled() -> bool:
        db.expire_all()
        return db.scalar(select(Business.texts_enabled).where(Business.slug == "pawcare-demo"))

    assert enabled() is False
    run("texts", "pawcare-demo", "on")
    assert enabled() is True
    assert "Customer texts for pawcare-demo are on." in capsys.readouterr().out
    run("texts", "pawcare-demo", "off")
    assert enabled() is False
    with pytest.raises(SystemExit, match="No business with slug nobody"):
        run("texts", "nobody", "on")
