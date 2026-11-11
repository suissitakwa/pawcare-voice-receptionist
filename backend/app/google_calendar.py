"""Two-way Google Calendar sync for the business's calendar.

- **Reading:** busy times in the calendar block booking times, through the free/busy API.
  Events this backend writes are marked "free", so a booking never blocks itself.
- **Writing:** every booking, change and cancellation is marked for sync. A background worker
  creates, updates or deletes one event per booking, with an event id derived from the
  booking's id, so a retry never creates a duplicate.

Access is through a Google service account. The business shares its calendar with the
account's email address ("Make changes to events"); no user sign-in is involved.
"""

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.errors import ApiError
from app.models import Booking, Business

logger = logging.getLogger("pawcare.calendar")

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"
BUSY_CACHE_SECONDS = 60
SYNC_BATCH = 20
POLL_SECONDS = 10


class CalendarError(Exception):
    """Google could not be reached or refused the request. Holds a status, never a body."""


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime


# send(method, url, body, headers) -> (status, parsed JSON or None). Replaced in tests.
Send = Callable[[str, str, bytes | None, dict], tuple[int, dict | None]]


def http_send(method: str, url: str, body: bytes | None, headers: dict) -> tuple[int, dict | None]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - fixed https URLs
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError) as error:
        raise CalendarError("Google Calendar could not be reached") from error


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_service_account(raw: str) -> dict:
    """The key file's JSON, pasted as is or base64-encoded (easier in some dashboards)."""
    text = raw.strip()
    if not text.startswith("{"):
        text = base64.b64decode(text).decode()
    account = json.loads(text)
    if not account.get("client_email") or not account.get("private_key"):
        raise ValueError("the service account JSON needs client_email and private_key")
    return account


class GoogleCalendar:
    def __init__(
        self, calendar_id: str, account: dict, send: Send = http_send, clock=time.time
    ) -> None:
        self.calendar_id = calendar_id
        self.email = account["client_email"]
        self.key = serialization.load_pem_private_key(account["private_key"].encode(), None)
        self.send = send
        self.clock = clock
        self.token: str | None = None
        self.token_expires = 0.0

    def assertion(self) -> str:
        now = int(self.clock())
        header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = b64url(
            json.dumps(
                {
                    "iss": self.email,
                    "scope": SCOPE,
                    "aud": TOKEN_URL,
                    "iat": now,
                    "exp": now + 3600,
                }
            ).encode()
        )
        signing_input = f"{header}.{claims}".encode()
        signature = self.key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{b64url(signature)}"

    def access_token(self) -> str:
        if self.token and self.clock() < self.token_expires - 60:
            return self.token
        body = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self.assertion(),
            }
        ).encode()
        status, data = self.send(
            "POST", TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"}
        )
        if status != 200 or not data or "access_token" not in data:
            raise CalendarError(f"token request returned HTTP {status}")
        self.token = data["access_token"]
        self.token_expires = self.clock() + int(data.get("expires_in", 3600))
        return self.token

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict | None]:
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        return self.send(method, API + path, body, headers)

    def events_path(self, event_id: str | None = None) -> str:
        path = f"/calendars/{urllib.parse.quote(self.calendar_id, safe='')}/events"
        return f"{path}/{event_id}" if event_id else path

    def busy(self, start: datetime, end: datetime) -> list[Busy]:
        status, data = self.call(
            "POST",
            "/freeBusy",
            {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self.calendar_id}],
            },
        )
        if status != 200 or data is None:
            raise CalendarError(f"free/busy returned HTTP {status}")
        calendar = data.get("calendars", {}).get(self.calendar_id, {})
        if calendar.get("errors"):
            # For example notFound: the calendar isn't shared with the service account.
            reasons = ",".join(e.get("reason", "?") for e in calendar["errors"])
            raise CalendarError(f"free/busy error: {reasons}")
        return [
            Busy(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in calendar.get("busy", [])
        ]

    def upsert(self, event_id: str, event: dict) -> None:
        status, _ = self.call("PUT", self.events_path(event_id), event)
        if status == 404:
            status, _ = self.call("POST", self.events_path(), {**event, "id": event_id})
        if status not in (200, 201):
            raise CalendarError(f"saving the event returned HTTP {status}")

    def delete(self, event_id: str) -> None:
        status, _ = self.call("DELETE", self.events_path(event_id))
        # Already gone counts as done.
        if status not in (200, 204, 404, 410):
            raise CalendarError(f"deleting the event returned HTTP {status}")


_clients: dict[tuple[str, str], GoogleCalendar] = {}


def calendar_for(settings: Settings) -> GoogleCalendar | None:
    if not settings.google_calendar_id or not settings.google_service_account_json:
        return None
    key = (settings.google_calendar_id, settings.google_service_account_json)
    if key not in _clients:
        account = load_service_account(settings.google_service_account_json)
        _clients[key] = GoogleCalendar(settings.google_calendar_id, account)
    return _clients[key]


# Availability asks this for the calendar; tests replace it.
current_calendar: Callable[[], GoogleCalendar | None] = lambda: calendar_for(get_settings())  # noqa: E731

_busy_cache: dict[tuple[str, datetime, datetime], tuple[float, list[Busy]]] = {}


def busy_between(start: datetime, end: datetime) -> list[Busy]:
    """Busy times from the business's calendar, or [] when no calendar is connected.

    If the calendar is connected but can't be read, booking stops rather than risk offering
    a time the owner is busy.
    """
    calendar = current_calendar()
    if calendar is None:
        return []
    key = (calendar.calendar_id, start, end)
    cached = _busy_cache.get(key)
    if cached and time.monotonic() - cached[0] < BUSY_CACHE_SECONDS:
        return cached[1]
    try:
        busy = calendar.busy(start, end)
    except CalendarError as error:
        logger.error("Could not read Google Calendar busy times: %s", error)
        raise ApiError(
            503,
            "calendar_unavailable",
            "The calendar can't be checked right now. Please try again shortly.",
        ) from None
    _busy_cache[key] = (time.monotonic(), busy)
    return busy


def clear_busy_cache() -> None:
    _busy_cache.clear()


def event_id(booking: Booking) -> str:
    # Google event ids may use only 0-9 and a-v, so "pc" plus the UUID's hex digits fits.
    return "pc" + booking.id.hex


def event_for(booking: Booking, business: Business) -> dict:
    timezone = ZoneInfo(business.timezone)
    summary = booking.summary or {}
    contact = " / ".join(c for c in (booking.owner_phone, booking.owner_email) if c)
    return {
        "summary": f"{summary.get('service', 'Grooming')}: {booking.pet_name} "
        f"({booking.owner_name})",
        "description": "\n".join(
            [
                f"Reference: {booking.reference}",
                f"Dog: {booking.pet_name}, {summary.get('size', '')}".rstrip(", "),
                f"Owner: {booking.owner_name}, {contact}",
                f"Price: ${booking.price_cents / 100:.2f}",
                f"Booked through {business.name}.",
            ]
        ),
        "start": {
            "dateTime": booking.starts_at.astimezone(timezone).isoformat(),
            "timeZone": business.timezone,
        },
        "end": {
            "dateTime": booking.ends_at.astimezone(timezone).isoformat(),
            "timeZone": business.timezone,
        },
        # "Free", so the booking doesn't block itself when busy times are read back.
        "transparency": "transparent",
        "extendedProperties": {"private": {"pawcare_reference": booking.reference}},
    }


def sync_due(session: Session, calendar: GoogleCalendar, now: datetime) -> int:
    """Pushes up to one batch of changed bookings to the calendar. Returns how many synced."""
    bookings = session.scalars(
        select(Booking)
        .where(
            Booking.calendar_dirty,
            or_(
                Booking.calendar_next_attempt_at.is_(None), Booking.calendar_next_attempt_at <= now
            ),
        )
        .order_by(Booking.created_at)
        .limit(SYNC_BATCH)
        .with_for_update(skip_locked=True)
        # Locked rows are read fresh, never from an earlier copy in this session.
        .execution_options(populate_existing=True)
    ).all()
    synced = 0
    for booking in bookings:
        business = session.get(Business, booking.business_id)
        try:
            if booking.status == "confirmed":
                calendar.upsert(event_id(booking), event_for(booking, business))
            else:
                calendar.delete(event_id(booking))
        except CalendarError as error:
            booking.calendar_attempts += 1
            # 1, 2, 4 … minutes, at most an hour, so a calendar that isn't shared yet
            # doesn't get hammered.
            delay = min(2 ** (booking.calendar_attempts - 1), 60)
            booking.calendar_next_attempt_at = now + timedelta(minutes=delay)
            booking.calendar_error = str(error)[:200]
            logger.warning("Calendar sync for %s failed: %s", booking.reference, error)
            continue
        booking.calendar_dirty = False
        booking.calendar_attempts = 0
        booking.calendar_next_attempt_at = None
        booking.calendar_error = None
        booking.calendar_synced_at = now
        synced += 1
    session.commit()
    return synced


async def run_sync_worker(settings: Settings, get_engine, clock) -> None:
    calendar = calendar_for(settings)
    if calendar is None:
        logger.info("Google Calendar is not connected")
        return
    logger.info("Google Calendar sync is on")

    def once() -> None:
        with Session(get_engine()) as session:
            sync_due(session, calendar, clock())

    while True:
        try:
            await asyncio.to_thread(once)
        except Exception:
            logger.exception("Calendar sync round failed")
        await asyncio.sleep(POLL_SECONDS)
