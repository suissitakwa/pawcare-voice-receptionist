"""Google Calendar: busy times block booking, and bookings are written as events.

Google is replaced by fakes: a fake calendar for availability and sync, and a fake HTTP
sender for the client itself (token signing, upsert, delete, free/busy).
"""

import base64
import json
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import google_calendar
from app.google_calendar import (
    Busy,
    CalendarError,
    GoogleCalendar,
    event_id,
    load_service_account,
    sync_due,
)
from app.main import app, get_now
from app.models import StaffUser
from tests.factories import make_booking
from tests.test_staff_api import PASSWORD, login

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)
TUESDAY = "2027-01-05"
NINE = datetime(2027, 1, 5, 9, 0, tzinfo=LA)


class FakeCalendar:
    calendar_id = "owner@example.com"

    def __init__(self, busy: list[Busy] | None = None, fail: bool = False) -> None:
        self.busy_times = busy or []
        self.fail = fail
        self.saved: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.busy_calls = 0

    def busy(self, start: datetime, end: datetime) -> list[Busy]:
        self.busy_calls += 1
        if self.fail:
            raise CalendarError("free/busy returned HTTP 500")
        return [b for b in self.busy_times if b.start < end and b.end > start]

    def upsert(self, event_id: str, event: dict) -> None:
        if self.fail:
            raise CalendarError("saving the event returned HTTP 403")
        self.saved[event_id] = event

    def delete(self, event_id: str) -> None:
        if self.fail:
            raise CalendarError("deleting the event returned HTTP 403")
        self.deleted.append(event_id)
        self.saved.pop(event_id, None)


@pytest.fixture
def calendar() -> Iterator[FakeCalendar]:
    fake = FakeCalendar()
    original = google_calendar.current_calendar
    google_calendar.current_calendar = lambda: fake
    google_calendar.clear_busy_cache()
    app.dependency_overrides[get_now] = lambda: NOW
    yield fake
    google_calendar.current_calendar = original
    google_calendar.clear_busy_cache()
    app.dependency_overrides.pop(get_now, None)


def starts(client: TestClient) -> list[str]:
    body = client.get(
        "/api/v1/availability",
        params={"service": "nail-and-paw", "size": "small", "date": TUESDAY},
    ).json()
    return [slot["local_start"][11:16] for slot in body["slots"]]


# Reading busy times


def test_busy_calendar_events_block_booking_times(client: TestClient, calendar) -> None:
    free = starts(client)
    assert "09:00" in free and "09:30" in free
    calendar.busy_times = [Busy(NINE, NINE + timedelta(hours=1))]
    google_calendar.clear_busy_cache()
    blocked = starts(client)
    assert "09:00" not in blocked and "09:30" not in blocked
    assert "10:00" in blocked


def test_busy_times_are_cached_briefly(client: TestClient, calendar) -> None:
    starts(client)
    starts(client)
    assert calendar.busy_calls == 1


def test_booking_stops_when_the_calendar_cannot_be_read(client: TestClient, calendar) -> None:
    calendar.fail = True
    response = client.get(
        "/api/v1/availability",
        params={"service": "nail-and-paw", "size": "small", "date": TUESDAY},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calendar_unavailable"


# Writing bookings as events


def test_a_booking_becomes_a_free_event_once(db: Session, calendar) -> None:
    booking = make_booking(db, NINE)
    assert sync_due(db, calendar, NOW) == 1
    event = calendar.saved[event_id(booking)]
    assert event_id(booking) == "pc" + booking.id.hex
    assert event["start"]["dateTime"].startswith("2027-01-05T09:00")
    assert event["transparency"] == "transparent"
    assert booking.reference in event["description"]
    db.refresh(booking)
    assert booking.calendar_dirty is False and booking.calendar_synced_at == NOW
    # Nothing left to do.
    assert sync_due(db, calendar, NOW) == 0


def test_a_cancelled_booking_deletes_its_event(db: Session, calendar) -> None:
    booking = make_booking(db, NINE)
    sync_due(db, calendar, NOW)
    booking.status = "cancelled"
    booking.calendar_dirty = True
    db.commit()
    sync_due(db, calendar, NOW)
    assert calendar.deleted == [event_id(booking)]
    assert event_id(booking) not in calendar.saved


def test_failed_syncs_back_off_and_keep_trying(db: Session, calendar) -> None:
    booking = make_booking(db, NINE)
    calendar.fail = True
    assert sync_due(db, calendar, NOW) == 0
    db.refresh(booking)
    assert booking.calendar_dirty and booking.calendar_attempts == 1
    assert booking.calendar_next_attempt_at == NOW + timedelta(minutes=1)
    assert "403" in booking.calendar_error
    calendar.fail = False
    assert sync_due(db, calendar, NOW + timedelta(seconds=30)) == 0
    assert sync_due(db, calendar, NOW + timedelta(minutes=1)) == 1
    db.refresh(booking)
    assert booking.calendar_error is None and booking.calendar_attempts == 0


def test_staff_cancelling_marks_the_booking_for_sync(
    client: TestClient, db: Session, calendar
) -> None:
    booking = make_booking(db, NINE)
    db.add(
        StaffUser(
            business_id=booking.business_id,
            email="maya@pawcare.example",
            display_name="Maya",
            password_hash=PasswordHasher().hash(PASSWORD),
        )
    )
    db.commit()
    sync_due(db, calendar, NOW)
    token = login(client).json()["token"]
    response = client.post(
        f"/api/v1/staff/bookings/{booking.reference}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    sync_due(db, calendar, NOW)
    assert calendar.deleted == [event_id(booking)]


# The Google client itself


def account() -> tuple[dict, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {"client_email": "sync@project.iam.gserviceaccount.com", "private_key": pem}, key


class FakeGoogle:
    def __init__(self, responses: dict[tuple[str, str], tuple[int, dict | None]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None, dict]] = []

    def __call__(self, method: str, url: str, body: bytes | None, headers: dict):
        self.requests.append((method, url, body, headers))
        if url == google_calendar.TOKEN_URL:
            return 200, {"access_token": "token-1", "expires_in": 3600}
        path = url.removeprefix(google_calendar.API)
        return self.responses.get((method, path), (200, {}))


def unpadded(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_the_token_request_is_signed_by_the_service_account() -> None:
    details, key = account()
    google = FakeGoogle({})
    client = GoogleCalendar("owner@example.com", details, send=google, clock=lambda: 1_800_000_000)
    client.delete("pcabc")
    client.delete("pcdef")
    token_requests = [r for r in google.requests if r[1] == google_calendar.TOKEN_URL]
    # One token, reused for both calls.
    assert len(token_requests) == 1
    assertion = urllib.parse.parse_qs(token_requests[0][2].decode())["assertion"][0]
    header, claims, signature = assertion.split(".")
    key.public_key().verify(
        unpadded(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    claims = json.loads(unpadded(claims))
    assert claims["iss"] == details["client_email"]
    assert claims["aud"] == google_calendar.TOKEN_URL
    assert claims["exp"] - claims["iat"] == 3600
    assert google.requests[1][3]["Authorization"] == "Bearer token-1"


def test_saving_creates_the_event_when_it_does_not_exist_yet() -> None:
    details, _ = account()
    events = "/calendars/owner%40example.com/events"
    google = FakeGoogle({("PUT", f"{events}/pc1"): (404, None), ("POST", events): (200, {})})
    GoogleCalendar("owner@example.com", details, send=google).upsert("pc1", {"summary": "x"})
    method, _, body, _ = google.requests[-1]
    assert method == "POST" and json.loads(body)["id"] == "pc1"


def test_deleting_an_event_that_is_already_gone_is_fine() -> None:
    details, _ = account()
    events = "/calendars/owner%40example.com/events"
    google = FakeGoogle({("DELETE", f"{events}/pc1"): (410, None)})
    GoogleCalendar("owner@example.com", details, send=google).delete("pc1")
    google = FakeGoogle({("DELETE", f"{events}/pc1"): (403, None)})
    with pytest.raises(CalendarError):
        GoogleCalendar("owner@example.com", details, send=google).delete("pc1")


def test_free_busy_is_read_and_sharing_errors_are_reported() -> None:
    details, _ = account()
    busy = {
        "calendars": {
            "owner@example.com": {
                "busy": [{"start": "2027-01-05T17:00:00Z", "end": "2027-01-05T18:00:00Z"}]
            }
        }
    }
    google = FakeGoogle({("POST", "/freeBusy"): (200, busy)})
    [period] = GoogleCalendar("owner@example.com", details, send=google).busy(NINE, NINE)
    assert period.start == NINE
    not_shared = {"calendars": {"owner@example.com": {"errors": [{"reason": "notFound"}]}}}
    google = FakeGoogle({("POST", "/freeBusy"): (200, not_shared)})
    with pytest.raises(CalendarError, match="notFound"):
        GoogleCalendar("owner@example.com", details, send=google).busy(NINE, NINE)


def test_the_key_can_be_pasted_as_json_or_base64() -> None:
    details, _ = account()
    raw = json.dumps(details)
    assert load_service_account(raw)["client_email"] == details["client_email"]
    assert load_service_account(base64.b64encode(raw.encode()).decode()) == details
    with pytest.raises(ValueError):
        load_service_account("{}")
