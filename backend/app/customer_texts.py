"""Texts customers about their bookings: confirmed, changed, cancelled, and a reminder.

Confirmation, change and cancellation texts read the same outbox as the webhook and owner
emails, so a text is only sent for a change that was saved. Reminders come from the bookings
themselves, a day ahead by default.

Only businesses with texts switched on send them (`python -m app.businesses texts <slug> on`),
from their own registered phone number through Twilio, or through a Twilio Messaging Service
when one is set. Every text names the business and says how to opt out.

At most once: Twilio has no idempotency key, so a request that timed out (and may have been
sent) is never retried. Refusals from Twilio, such as a customer who replied STOP, are final.
Errors that show the text wasn't sent (Twilio down, rate limited) are retried a few times.
"""

import asyncio
import base64
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.format_text import format_time
from app.models import Booking, Business, BusinessPhoneNumber, WebhookEvent
from app.phone_numbers import normalize
from app.webhooks import booking_data

logger = logging.getLogger("pawcare.texts")

TEXTED_EVENTS = {"booking.confirmed", "booking.changed", "booking.cancelled"}
MAX_ATTEMPTS = 4
FIRST_RETRY = timedelta(minutes=1)
MAX_RETRY = timedelta(minutes=30)
# A confirmation that arrives hours late confuses more than it helps.
GIVE_UP_AFTER = timedelta(hours=6)
# A reminder this close to the appointment isn't worth sending.
REMINDER_CUTOFF = timedelta(hours=1)
BATCH = 20
POLL_SECONDS = 10
TIMEOUT_SECONDS = 10
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
OPT_OUT = "Reply STOP to opt out."


@dataclass(frozen=True)
class Text:
    to: str
    body: str
    # Exactly one of these.
    from_number: str | None = None
    messaging_service_sid: str | None = None


class Retry(Exception):
    """Not sent, and worth trying again."""


class Refused(Exception):
    """Not sent, or perhaps sent, and not to be tried again."""


Texter = Callable[[Settings, Text], None]


def texting_problem(settings: Settings) -> str | None:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        return "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not set"
    return None


def sender(session: Session, business: Business, settings: Settings) -> dict | None:
    """Where the business's texts come from: a Messaging Service, or its own number."""
    if settings.twilio_messaging_service_sid:
        return {"messaging_service_sid": settings.twilio_messaging_service_sid}
    number = session.scalar(
        select(BusinessPhoneNumber.number)
        .where(BusinessPhoneNumber.business_id == business.id)
        .order_by(BusinessPhoneNumber.created_at)
        .limit(1)
    )
    if number is None and business.slug == settings.business_slug and settings.phone_number:
        number = settings.phone_number
    return {"from_number": number} if number else None


def texts_customers(session: Session, business: Business, settings: Settings) -> bool:
    """Whether a customer who gives a phone number will really be texted."""
    return (
        business.texts_enabled
        and texting_problem(settings) is None
        and sender(session, business, settings) is not None
    )


def when(local_iso: str) -> str:
    start = datetime.fromisoformat(local_iso)
    return f"{start:%a, %b} {start.day} at {format_time(start.time())}"


def compose(kind: str, data: dict, business: Business) -> str:
    """One short text. kind is an event type, or "reminder"."""
    what = data.get("service") or "Your appointment"
    if data.get("pet_name"):
        what += f" for {data['pet_name']}"
    if data.get("staff"):
        what += f" with {data['staff']}"
    start = when(data["local_start"])
    place = f" at {data['service_address']}" if data.get("service_address") else ""
    call = f"call {business.contact_phone}" if business.contact_phone else "contact us"
    reference = f"Ref {data.get('reference')}."
    if kind == "booking.confirmed":
        lines = [f"{business.name}: you're booked. {what}, {start}{place}.", reference]
        lines.append(f"To change it, {call}.")
    elif kind == "booking.changed":
        lines = [f"{business.name}: your booking is updated. {what}, {start}{place}.", reference]
        lines.append(f"To change it, {call}.")
    elif kind == "booking.cancelled":
        lines = [f"{business.name}: your booking is cancelled. {what}, {start}.", reference]
        lines.append(f"To book again, {call}.")
    else:
        lines = [f"Reminder from {business.name}: {what}, {start}{place}.", reference]
        lines.append(f"To change it, {call}.")
    return " ".join([*lines, OPT_OUT])


def send_twilio(settings: Settings, text: Text) -> None:
    fields = {"To": text.to, "Body": text.body}
    if text.messaging_service_sid:
        fields["MessagingServiceSid"] = text.messaging_service_sid
    else:
        fields["From"] = text.from_number
    credentials = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}"
    # Twilio's fixed https address; only the account SID from settings goes into it.
    request = urllib.request.Request(  # noqa: S310
        TWILIO_URL.format(sid=urllib.parse.quote(settings.twilio_account_sid, safe="")),
        data=urllib.parse.urlencode(fields).encode(),
        method="POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "receptionist-texts/1",
        },
    )
    try:
        # A fixed https address, never one from input.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            if response.status >= 300:
                raise Retry(f"Twilio answered HTTP {response.status}")
    except urllib.error.HTTPError as error:
        raw = error.read(500).decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
            reason = f"Twilio {detail.get('code')}: {detail.get('message')}"
        except ValueError:
            reason = f"Twilio answered HTTP {error.code}"
        if error.code == 429 or error.code >= 500:
            raise Retry(reason) from None
        raise Refused(reason) from None
    except TimeoutError:
        raise Refused("no answer from Twilio in time; it may have been sent") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise Refused("no answer from Twilio in time; it may have been sent") from None
        raise Retry(f"Twilio can't be reached: {error.reason}") from None


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * (2 ** (attempts - 1)), MAX_RETRY)


def prepare(
    session: Session, business: Business, settings: Settings, phone: str | None, body: str
) -> Text | str:
    """The text to send, or why there is none."""
    if not business.texts_enabled:
        return "texts are off for this business"
    to = normalize(phone)
    if to is None:
        return "no phone number that can be texted"
    source = sender(session, business, settings)
    if source is None:
        return "the business has no number to text from"
    return Text(to=to, body=body, **source)


def text_events(session: Session, settings: Settings, now: datetime, texter: Texter) -> int:
    events = session.scalars(
        select(WebhookEvent)
        .where(
            WebhookEvent.event_type.in_(TEXTED_EVENTS),
            WebhookEvent.text_sent_at.is_(None),
            WebhookEvent.text_failed_at.is_(None),
            or_(
                WebhookEvent.text_next_attempt_at.is_(None),
                WebhookEvent.text_next_attempt_at <= now,
            ),
        )
        .order_by(WebhookEvent.created_at)
        .limit(BATCH)
        # Several workers can share the outbox without texting twice.
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for event in events:
        business = session.get(Business, event.business_id)
        data = event.payload.get("data", {})
        body = compose(event.event_type, data, business)
        text = prepare(session, business, settings, data.get("owner_phone"), body)
        if isinstance(text, str):
            event.text_failed_at, event.text_error = now, text
            continue
        if event.created_at < now - GIVE_UP_AFTER:
            event.text_failed_at, event.text_error = now, "not sent in time"
            continue
        if datetime.fromisoformat(data["starts_at"]) <= now:
            event.text_failed_at, event.text_error = now, "the appointment has started"
            continue
        event.text_attempts += 1
        try:
            texter(settings, text)
        except Retry as error:
            event.text_error = str(error)[:200]
            if event.text_attempts >= MAX_ATTEMPTS:
                event.text_failed_at = now
                logger.warning("Text for event %s failed: %s", event.id, error)
            else:
                event.text_next_attempt_at = now + retry_delay(event.text_attempts)
            continue
        except Refused as error:
            event.text_failed_at, event.text_error = now, str(error)[:200]
            logger.warning("Text for event %s refused: %s", event.id, error)
            continue
        event.text_sent_at, event.text_error = now, None
        sent += 1
    return sent


def finish_reminder(booking: Booking, error: str | None) -> None:
    booking.reminder_for = booking.starts_at
    booking.reminder_attempts = 0
    booking.reminder_next_attempt_at = None
    booking.reminder_error = error


def text_reminders(session: Session, settings: Settings, now: datetime, texter: Texter) -> int:
    lead = timedelta(hours=settings.reminder_hours_before)
    rows = session.execute(
        select(Booking, Business)
        .join(Business, Business.id == Booking.business_id)
        .where(
            Business.texts_enabled.is_(True),
            Booking.status == "confirmed",
            Booking.owner_phone.is_not(None),
            Booking.starts_at > now + REMINDER_CUTOFF,
            Booking.starts_at <= now + lead,
            or_(Booking.reminder_for.is_(None), Booking.reminder_for != Booking.starts_at),
            or_(
                Booking.reminder_next_attempt_at.is_(None),
                Booking.reminder_next_attempt_at <= now,
            ),
        )
        .order_by(Booking.starts_at)
        .limit(BATCH)
        .with_for_update(of=Booking, skip_locked=True)
    ).all()
    sent = 0
    for booking, business in rows:
        # Booked (or moved) inside the reminder window: the confirmation just said it all.
        if (booking.changed_at or booking.created_at) > booking.starts_at - lead:
            finish_reminder(booking, "booked within the reminder window")
            continue
        data = booking_data(booking, business)
        text = prepare(
            session, business, settings, booking.owner_phone, compose("reminder", data, business)
        )
        if isinstance(text, str):
            finish_reminder(booking, text)
            continue
        booking.reminder_attempts += 1
        try:
            texter(settings, text)
        except Retry as error:
            if booking.reminder_attempts >= MAX_ATTEMPTS:
                finish_reminder(booking, str(error)[:200])
                logger.warning("Reminder for %s failed: %s", booking.reference, error)
            else:
                booking.reminder_error = str(error)[:200]
                booking.reminder_next_attempt_at = now + retry_delay(booking.reminder_attempts)
            continue
        except Refused as error:
            finish_reminder(booking, str(error)[:200])
            logger.warning("Reminder for %s refused: %s", booking.reference, error)
            continue
        finish_reminder(booking, None)
        sent += 1
    return sent


def send_due(
    session: Session, settings: Settings, now: datetime, texter: Texter = send_twilio
) -> int:
    """Sends the texts that are due. Returns how many were sent."""
    if texting_problem(settings) is not None:
        return 0
    sent = text_events(session, settings, now, texter)
    session.commit()
    sent += text_reminders(session, settings, now, texter)
    session.commit()
    return sent


async def run_text_worker(settings: Settings, get_engine, clock) -> None:
    """Sends customer texts every few seconds until cancelled."""
    problem = texting_problem(settings)
    if problem is not None:
        logger.info("Customer texts are off: %s.", problem)
        return

    def once() -> None:
        with Session(get_engine()) as session:
            send_due(session, settings, clock())

    while True:
        try:
            await asyncio.to_thread(once)
        except Exception:
            logger.exception("Customer text round failed")
        await asyncio.sleep(POLL_SECONDS)
