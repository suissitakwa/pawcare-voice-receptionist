"""Events for the business's automation webhook, such as an n8n workflow.

Events are written to an outbox in the same transaction as the change they describe, so an
event is never sent for something that did not happen, and never lost because the webhook
was down. A worker delivers them, signed, and retries with growing delays.

Each request carries:
  X-Webhook-Id         the event id, the same on every retry, for removing duplicates
  X-Webhook-Event      the event type, for example booking.confirmed
  X-Webhook-Timestamp  Unix seconds when this attempt was signed
  X-Webhook-Signature  sha256=<hex HMAC-SHA256 of "<timestamp>.<body>" with the secret>
"""

import asyncio
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Booking, Business, CallbackRequest, WebhookEvent

logger = logging.getLogger("pawcare.webhooks")

MAX_ATTEMPTS = 8
FIRST_RETRY = timedelta(seconds=30)
MAX_RETRY = timedelta(hours=1)
# A notification that arrives a day late does more harm than good.
DELIVERY_WINDOW = timedelta(hours=24)
# Events hold customers' contact details, so they are not kept longer than this.
RETENTION = timedelta(days=30)
BATCH = 20
TIMEOUT_SECONDS = 5
POLL_SECONDS = 5
MIN_SECRET_LENGTH = 32
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def enqueue(
    session: Session, business_id: uuid.UUID, event_type: str, data: dict, now: datetime
) -> WebhookEvent:
    """Adds an event to the caller's transaction. It is sent only if that transaction commits."""
    event = WebhookEvent(
        id=uuid.uuid4(),
        business_id=business_id,
        event_type=event_type,
        payload={},
        created_at=now,
        next_attempt_at=now,
    )
    business = session.get(Business, business_id)
    event.payload = {
        "id": str(event.id),
        "type": event_type,
        "occurred_at": now.isoformat(),
        "business": {
            "slug": business.slug,
            "name": business.name,
            "timezone": business.timezone,
        },
        "data": data,
    }
    session.add(event)
    return event


def booking_data(booking: Booking, business: Business, source: str | None = None) -> dict:
    timezone = ZoneInfo(business.timezone)
    summary = booking.summary or {}
    return {
        "reference": booking.reference,
        "status": booking.status,
        "service": summary.get("service"),
        "size": summary.get("size"),
        "starts_at": booking.starts_at.isoformat(),
        "local_start": booking.starts_at.astimezone(timezone).isoformat(),
        "local_end": booking.ends_at.astimezone(timezone).isoformat(),
        "price_cents": booking.price_cents,
        "pet_name": booking.pet_name,
        "owner_name": booking.owner_name,
        "owner_email": booking.owner_email,
        "owner_phone": booking.owner_phone,
        "source": source or ("voice" if booking.voice_session_id else "form"),
    }


def callback_data(callback: CallbackRequest) -> dict:
    return {
        "id": str(callback.id),
        "name": callback.name,
        "phone": callback.phone,
        "email": callback.email,
        "reason": callback.reason,
        "note": callback.note,
    }


def configuration_problem(settings: Settings) -> str | None:
    """Why the webhook cannot be used, or None when it can."""
    if not settings.webhook_url:
        return "no webhook URL is set"
    parsed = urlparse(settings.webhook_url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return "the webhook URL is not a web address"
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        return "the webhook URL must use https"
    if len(settings.webhook_secret) < MIN_SECRET_LENGTH:
        return f"the webhook secret must be at least {MIN_SECRET_LENGTH} characters"
    return None


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


@dataclass(frozen=True)
class Attempt:
    status: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


Sender = Callable[[str, bytes, dict[str, str]], Attempt]


def send_http(url: str, body: bytes, headers: dict[str, str]) -> Attempt:
    # Only http(s) URLs reach here: configuration_problem rejects every other scheme.
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return Attempt(status=response.status)
    except urllib.error.HTTPError as error:
        return Attempt(status=error.code, error=f"HTTP {error.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # The reason only: never the response body, which could echo the payload.
        return Attempt(status=None, error=type(error).__name__)


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * (2 ** (attempts - 1)), MAX_RETRY)


@dataclass
class DeliveryReport:
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    expired: int = 0
    removed: int = 0


def deliver_due(
    session: Session, settings: Settings, now: datetime, send: Sender = send_http
) -> DeliveryReport:
    """Sends events that are due, records each outcome, and removes old events."""
    report = DeliveryReport()
    report.removed = session.execute(
        delete(WebhookEvent).where(WebhookEvent.created_at < now - RETENTION)
    ).rowcount
    pending = WebhookEvent.delivered_at.is_(None) & WebhookEvent.failed_at.is_(None)
    for event in session.scalars(
        select(WebhookEvent).where(pending, WebhookEvent.created_at < now - DELIVERY_WINDOW)
    ):
        event.failed_at = now
        event.last_error = "not delivered within 24 hours"
        report.expired += 1
    session.commit()

    problem = configuration_problem(settings)
    if problem is not None:
        return report

    # SKIP LOCKED lets several workers share the outbox without sending an event twice.
    events = session.scalars(
        select(WebhookEvent)
        .where(pending, WebhookEvent.next_attempt_at <= now)
        .order_by(WebhookEvent.created_at)
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    for event in events:
        body = json.dumps(event.payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = int(now.timestamp())
        attempt = send(
            settings.webhook_url,
            body,
            {
                "Content-Type": "application/json",
                "User-Agent": "receptionist-webhooks/1",
                "X-Webhook-Id": str(event.id),
                "X-Webhook-Event": event.event_type,
                "X-Webhook-Timestamp": str(timestamp),
                "X-Webhook-Signature": sign(settings.webhook_secret, timestamp, body),
            },
        )
        event.attempts += 1
        if attempt.ok:
            event.delivered_at = now
            event.last_error = None
            report.delivered += 1
        elif event.attempts >= MAX_ATTEMPTS:
            event.failed_at = now
            event.last_error = attempt.error or f"HTTP {attempt.status}"
            report.failed += 1
            logger.warning("Webhook event %s failed after %s attempts", event.id, event.attempts)
        else:
            event.next_attempt_at = now + retry_delay(event.attempts)
            event.last_error = attempt.error or f"HTTP {attempt.status}"
            report.retried += 1
    session.commit()
    return report


async def run_worker(settings: Settings, get_engine, clock) -> None:
    """Delivers events every few seconds until cancelled."""
    problem = configuration_problem(settings)
    if problem is not None:
        logger.info("Webhook delivery is off: %s. Events are kept for 30 days.", problem)

    def once() -> None:
        with Session(get_engine()) as session:
            deliver_due(session, settings, clock())

    while True:
        try:
            await asyncio.to_thread(once)
        except Exception:
            # A database hiccup must not stop delivery for good.
            logger.exception("Webhook delivery round failed")
        await asyncio.sleep(POLL_SECONDS)
