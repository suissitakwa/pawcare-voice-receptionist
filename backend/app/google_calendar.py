"""Two-way Google Calendar sync, one calendar per business (see app.integrations).

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
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import func, or_, select, true, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.errors import ApiError
from app.integrations import (
    CALENDAR,
    CalendarConfig,
    IntegrationUnavailable,
    businesses_with,
    calendar_config,
)
from app.models import Booking, Business, BusinessIntegration

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


class CalendarAccessProblem(Exception):
    """Why a calendar can't be connected, in words for the owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def shared_account_email(settings: Settings) -> str | None:
    """The address owners share their calendar with: HelloEver's service account."""
    if not settings.google_service_account_json:
        return None
    try:
        return load_service_account(settings.google_service_account_json)["client_email"]
    except (ValueError, KeyError):
        logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON can't be read")
        return None


WRITABLE = ("owner", "writer")


def check_access(settings: Settings, calendar_id: str, send: Send = http_send) -> None:
    """Checks that HelloEver's account can make changes to this calendar. Raises
    CalendarAccessProblem otherwise.

    Adding the calendar to the account's own list answers with the access the owner gave, and
    fails when nothing was shared. The list entry is harmless and is how Google expects a shared
    calendar to be used.
    """
    if shared_account_email(settings) is None:
        raise CalendarAccessProblem(
            "calendar_not_offered",
            "Connecting a calendar isn't available yet. We'll do it with you.",
        )
    check_account_access(settings.google_service_account_json, calendar_id, send)


def check_account_access(account_json: str, calendar_id: str, send: Send = http_send) -> None:
    """Checks that this service account can make changes to the calendar. Raises
    CalendarAccessProblem otherwise, in words for the owner."""
    calendar = GoogleCalendar(calendar_id, load_service_account(account_json), send)
    email = calendar.email
    try:
        status, data = calendar.call("POST", "/users/me/calendarList", {"id": calendar_id})
        if status == 409:
            path = "/users/me/calendarList/" + urllib.parse.quote(calendar_id, safe="")
            status, data = calendar.call("GET", path)
    except CalendarError:
        raise CalendarAccessProblem(
            "calendar_unreachable", "Google Calendar couldn't be reached. Try again in a minute."
        ) from None
    if status in (403, 404) or (status == 200 and not data):
        raise CalendarAccessProblem(
            "calendar_not_shared",
            f"We can't see that calendar yet. Share it with {email}, choose \"Make changes to "
            'events", and check the calendar ID.',
        )
    if status != 200 or data is None:
        raise CalendarAccessProblem(
            "calendar_unreachable", "Google Calendar couldn't be reached. Try again in a minute."
        )
    if data.get("accessRole") not in WRITABLE:
        raise CalendarAccessProblem(
            "calendar_read_only",
            f'The calendar is shared, but only to see it. Change {email} to "Make changes to '
            'events" so bookings can be added.',
        )


@dataclass(frozen=True)
class CalendarStatus:
    connected: bool
    calendar_id: str | None
    # From a live check with Google: None when the calendar can be read and changed.
    problem_code: str | None
    problem: str | None
    # Bookings still to be written to (or removed from) the calendar.
    waiting: int
    last_synced_at: datetime | None
    last_error: str | None


UNREADABLE = (
    "calendar_settings_unreadable",
    "Your calendar connection can't be read on our side, so booking is paused. Please contact us.",
)


def calendar_status(session: Session, settings: Settings, business_id: uuid.UUID) -> CalendarStatus:
    """Whether the business's calendar works right now, checked with Google, and how syncing
    its bookings is going."""
    mine = Booking.business_id == business_id
    waiting = session.scalar(
        select(func.count()).select_from(Booking).where(mine, Booking.calendar_dirty)
    )
    last_synced_at = session.scalar(select(func.max(Booking.calendar_synced_at)).where(mine))
    last_error = session.scalar(
        select(Booking.calendar_error)
        .where(mine, Booking.calendar_dirty, Booking.calendar_error.is_not(None))
        # The latest failure has the latest retry.
        .order_by(Booking.calendar_next_attempt_at.desc().nulls_last())
        .limit(1)
    )
    try:
        config = calendar_config(session, settings, business_id)
    except IntegrationUnavailable as error:
        logger.error("Calendar settings for business %s can't be read: %s", business_id, error)
        return CalendarStatus(True, None, *UNREADABLE, waiting, last_synced_at, last_error)
    if config is None:
        return CalendarStatus(False, None, None, None, waiting, last_synced_at, None)
    code = message = None
    try:
        check_account_access(config.service_account_json, config.calendar_id)
    except CalendarAccessProblem as problem:
        code, message = problem.code, problem.message
    except (ValueError, KeyError) as error:
        logger.error("Calendar key for business %s can't be used: %s", business_id, error)
        code, message = UNREADABLE
    return CalendarStatus(
        True, config.calendar_id, code, message, waiting, last_synced_at, last_error
    )


_clients: dict[tuple[str, str], GoogleCalendar] = {}


def client_for(config: CalendarConfig) -> GoogleCalendar:
    key = (config.calendar_id, config.service_account_json)
    if key not in _clients:
        account = load_service_account(config.service_account_json)
        _clients[key] = GoogleCalendar(config.calendar_id, account)
    return _clients[key]


def calendar_of(session: Session, business: Business) -> GoogleCalendar | None:
    """The business's own calendar client, or None when it has no calendar."""
    config = calendar_config(session, get_settings(), business.id)
    return client_for(config) if config is not None else None


# Availability asks this for a business's calendar; tests replace it.
current_calendar: Callable[[Session, Business], GoogleCalendar | None] = calendar_of

_busy_cache: dict[tuple[str, datetime, datetime], tuple[float, list[Busy]]] = {}


def calendar_unavailable() -> ApiError:
    return ApiError(
        503,
        "calendar_unavailable",
        "The calendar can't be checked right now. Please try again shortly.",
    )


def busy_between(
    session: Session, business: Business, start: datetime, end: datetime
) -> list[Busy]:
    """Busy times from the business's calendar, or [] when it has no calendar connected.

    If the calendar is connected but can't be read, booking stops rather than risk offering
    a time the owner is busy.
    """
    try:
        calendar = current_calendar(session, business)
    except (IntegrationUnavailable, ValueError, KeyError) as error:
        logger.error("Google Calendar settings for %s can't be used: %s", business.slug, error)
        raise calendar_unavailable() from None
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
        raise calendar_unavailable() from None
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
        "summary": f"{summary.get('service', 'Appointment')}: {booking.pet_name} "
        f"({booking.owner_name})"
        if booking.pet_name
        else f"{summary.get('service', 'Appointment')}: {booking.owner_name}",
        "description": "\n".join(
            [
                f"Reference: {booking.reference}",
                *(
                    [
                        f"Dog: {booking.pet_name}, {summary.get('size', '')}".rstrip(", "),
                        f"Owner: {booking.owner_name}, {contact}",
                    ]
                    if booking.pet_name
                    else [f"Client: {booking.owner_name}, {contact}"]
                ),
                *([f"With: {summary['staff']}"] if summary.get("staff") else []),
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
        # A mobile groomer's next stop shows on their calendar and maps.
        **({"location": booking.service_address} if booking.service_address else {}),
        # "Free", so the booking doesn't block itself when busy times are read back.
        "transparency": "transparent",
        "extendedProperties": {"private": {"pawcare_reference": booking.reference}},
    }


def sync_due(
    session: Session,
    calendar: GoogleCalendar,
    now: datetime,
    business_id: uuid.UUID | None = None,
) -> int:
    """Pushes up to one batch of one business's changed bookings to its calendar. Returns how
    many synced. Without a business id (tests only), every business's bookings."""
    bookings = session.scalars(
        select(Booking)
        .where(
            Booking.business_id == business_id if business_id is not None else true(),
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


def sync_round(session: Session, settings: Settings, now: datetime) -> int:
    """One pass over every business with a calendar. Returns how many bookings synced."""
    synced = 0
    for business_id in businesses_with(session, settings, CALENDAR):
        try:
            config = calendar_config(session, settings, business_id)
            if config is None:
                continue
            calendar = client_for(config)
        except (IntegrationUnavailable, ValueError, KeyError) as error:
            logger.warning("Skipping calendar sync for business %s: %s", business_id, error)
            continue
        synced += sync_due(session, calendar, now, business_id)
    return synced


CHECK_EVERY = timedelta(minutes=15)
# Google being briefly unreachable isn't worth an email; a calendar that isn't shared is.
UNREACHABLE_GRACE = timedelta(minutes=30)
CHECK_BATCH = 20


def check_problem(
    session: Session, settings: Settings, business_id: uuid.UUID
) -> tuple[str, str] | None:
    """What's wrong with the business's calendar right now, or None when it works."""
    try:
        config = calendar_config(session, settings, business_id)
        if config is None:
            return None
        check_account_access(config.service_account_json, config.calendar_id)
    except CalendarAccessProblem as problem:
        return problem.code, problem.message
    except (IntegrationUnavailable, ValueError, KeyError) as error:
        logger.error("Calendar settings for business %s can't be used: %s", business_id, error)
        return UNREADABLE
    return None


def alert(
    session: Session,
    row: BusinessIntegration,
    problem: tuple[str, str] | None,
    since: datetime | None,
    now: datetime,
) -> None:
    """Tells the owner (email and webhook) and HelloEver's admin (email) that the calendar
    stopped working (with the problem) or works again (problem None)."""
    from app.webhooks import enqueue, enqueue_notice

    working = problem is None
    business = session.get(Business, row.business_id)
    data = {
        "calendar_id": row.config.get("calendar_id"),
        "problem_code": None if working else problem[0],
        "problem": None if working else problem[1],
        "since": since.isoformat() if since else None,
    }
    enqueue(session, business.id, "calendar.working" if working else "calendar.failing", data, now)
    enqueue_notice(
        session,
        "calendar.alert",
        {
            **data,
            "working": working,
            "business_name": business.name,
            "business_slug": business.slug,
        },
        now,
    )


def check_round(session: Session, settings: Settings, now: datetime) -> int:
    """Checks each stored calendar that's due (every 15 minutes) and emails once when one
    starts failing and once when it works again. Returns how many were checked."""
    rows = session.scalars(
        select(BusinessIntegration)
        .where(
            BusinessIntegration.kind == CALENDAR,
            or_(
                BusinessIntegration.checked_at.is_(None),
                BusinessIntegration.checked_at <= now - CHECK_EVERY,
            ),
        )
        .order_by(BusinessIntegration.checked_at.asc().nulls_first())
        .limit(CHECK_BATCH)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for row in rows:
        found = check_problem(session, settings, row.business_id)
        changes: dict = {"checked_at": now}
        if found is None:
            if row.alerted_at is not None:
                alert(session, row, None, row.problem_since, now)
            changes |= {
                "problem_code": None,
                "problem": None,
                "problem_since": None,
                "alerted_at": None,
            }
        else:
            code, message = found
            since = row.problem_since or now
            changes |= {"problem_code": code, "problem": message[:300], "problem_since": since}
            lasting = code != "calendar_unreachable" or now - since >= UNREACHABLE_GRACE
            if row.alerted_at is None and lasting:
                alert(session, row, found, since, now)
                changes["alerted_at"] = now
            logger.warning("Calendar for business %s: %s", row.business_id, code)
        # Keeps updated_at as when it was connected.
        session.execute(
            update(BusinessIntegration)
            .where(BusinessIntegration.id == row.id)
            .values(**changes, updated_at=BusinessIntegration.updated_at)
            .execution_options(synchronize_session=False)
        )
    session.commit()
    return len(rows)


async def run_sync_worker(settings: Settings, get_engine, clock) -> None:
    """Syncs every business's calendar every few seconds until cancelled, and checks each one
    every 15 minutes. Calendars connected while the server runs are picked up on the next
    round."""

    def once() -> None:
        with Session(get_engine()) as session:
            sync_round(session, settings, clock())
            check_round(session, settings, clock())

    while True:
        try:
            await asyncio.to_thread(once)
        except Exception:
            logger.exception("Calendar sync round failed")
        await asyncio.sleep(POLL_SECONDS)
