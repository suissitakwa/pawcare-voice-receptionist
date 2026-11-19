"""Emails the owner about new, changed and cancelled bookings and callback requests.

It reads the same outbox as the webhook, so an email is only sent for a change that was
saved, and a mail server being down only delays it. Each event is emailed once, retried with
growing delays, and given up after a day: an old notice does more harm than good.

The demo business never emails, unless a notification address is set for it on purpose.
"""

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.format_text import format_time
from app.models import Business, WebhookEvent

logger = logging.getLogger("pawcare.email")

EMAILED_EVENTS = {"booking.confirmed", "booking.changed", "booking.cancelled", "callback.requested"}
MAX_ATTEMPTS = 6
FIRST_RETRY = timedelta(minutes=1)
MAX_RETRY = timedelta(hours=1)
GIVE_UP_AFTER = timedelta(hours=24)
BATCH = 20
POLL_SECONDS = 10
TIMEOUT_SECONDS = 15
REASONS = {
    "health": "health question",
    "behavior": "behavior question",
    "special_handling": "special handling",
    "large_dog": "large dog",
    "booking_help": "help with booking",
    "other": "other",
}

Mailer = Callable[[Settings, EmailMessage], None]


def email_problem(settings: Settings) -> str | None:
    if not settings.smtp_host:
        return "SMTP_HOST is not set"
    if not parseaddr(settings.email_from)[1]:
        return "EMAIL_FROM is not set"
    return None


def owner_address(business: Business) -> str | None:
    if business.notify_email:
        return business.notify_email
    return None if business.is_demo else business.contact_email


def when(local_iso: str) -> str:
    start = datetime.fromisoformat(local_iso)
    return f"{start:%A, %B} {start.day} at {format_time(start.time())}"


def compose(event: WebhookEvent, business: Business, settings: Settings) -> tuple[str, str]:
    """Subject and plain-text body for one event."""
    data = event.payload.get("data", {})
    dashboard = (
        f"{settings.public_site_url.rstrip('/')}/b/{business.slug}/staff"
        if settings.public_site_url
        else None
    )
    if event.event_type == "callback.requested":
        reason = REASONS.get(data.get("reason"), data.get("reason") or "")
        subject = f"Please call back: {data.get('name')} ({reason})"
        lines = [
            f"{data.get('name')} asked the receptionist for a call back.",
            "",
            f"Phone: {data.get('phone') or '-'}",
            f"Email: {data.get('email') or '-'}",
            f"Reason: {reason}",
        ]
        if data.get("note"):
            lines.append(f"Note: {data['note']}")
    else:
        action = {
            "booking.confirmed": "New booking",
            "booking.changed": "Booking changed",
            "booking.cancelled": "Booking cancelled",
        }[event.event_type]
        start = when(data["local_start"])
        subject = f"{action}: {data.get('service')} for {data.get('pet_name')}, {start}"
        contact = ", ".join(c for c in (data.get("owner_phone"), data.get("owner_email")) if c)
        lines = [
            f"{action} ({'by phone or voice' if data.get('source') == 'voice' else 'online'}).",
            "",
            f"When: {start}",
            f"Service: {data.get('service')}, {str(data.get('size', '')).lower()} dog",
            f"Dog: {data.get('pet_name')}",
            f"Owner: {data.get('owner_name')}, {contact}",
        ]
        if data.get("staff"):
            lines.append(f"With: {data['staff']}")
        lines += [
            f"Price: ${data.get('price_cents', 0) / 100:.2f}",
            f"Reference: {data.get('reference')}",
        ]
    if dashboard:
        lines += ["", f"Staff dashboard: {dashboard}"]
    lines += ["", f"Sent by {business.name}'s AI receptionist."]
    return subject, "\n".join(lines)


def message_for(event: WebhookEvent, business: Business, settings: Settings, to: str):
    subject, body = compose(event, business, settings)
    message = EmailMessage()
    sender_name, sender = parseaddr(settings.email_from)
    message["From"] = formataddr((sender_name or f"{business.name} receptionist", sender))
    message["To"] = to
    message["Subject"] = subject
    # The same id on every retry, so a mail server that saw it once can drop a duplicate.
    message["Message-ID"] = make_msgid(idstring=str(event.id), domain=sender.split("@")[-1])
    message.set_content(body)
    return message


def send_smtp(settings: Settings, message: EmailMessage) -> None:
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_SECONDS)
    with server:
        if settings.smtp_port != 465:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * (2 ** (attempts - 1)), MAX_RETRY)


def send_due(session: Session, settings: Settings, now: datetime, mail: Mailer = send_smtp) -> int:
    """Emails owners about events that are due. Returns how many were sent."""
    if email_problem(settings) is not None:
        return 0
    events = session.scalars(
        select(WebhookEvent)
        .where(
            WebhookEvent.event_type.in_(EMAILED_EVENTS),
            WebhookEvent.email_sent_at.is_(None),
            WebhookEvent.email_failed_at.is_(None),
            (WebhookEvent.email_next_attempt_at.is_(None))
            | (WebhookEvent.email_next_attempt_at <= now),
        )
        .order_by(WebhookEvent.created_at)
        .limit(BATCH)
        # Several workers can share the outbox without emailing twice.
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for event in events:
        business = session.get(Business, event.business_id)
        to = owner_address(business)
        if to is None:
            event.email_failed_at = now
            event.email_error = "no address to notify"
            continue
        if event.created_at < now - GIVE_UP_AFTER:
            event.email_failed_at = now
            event.email_error = "not sent within 24 hours"
            continue
        try:
            mail(settings, message_for(event, business, settings, to))
        except (smtplib.SMTPException, OSError) as error:
            event.email_attempts += 1
            event.email_error = str(error)[:200]
            if event.email_attempts >= MAX_ATTEMPTS:
                event.email_failed_at = now
                logger.warning("Owner email for event %s failed: %s", event.id, error)
            else:
                event.email_next_attempt_at = now + retry_delay(event.email_attempts)
            continue
        event.email_attempts += 1
        event.email_sent_at = now
        event.email_error = None
        sent += 1
    session.commit()
    return sent


async def run_email_worker(settings: Settings, get_engine, clock) -> None:
    """Sends owner emails every few seconds until cancelled."""
    problem = email_problem(settings)
    if problem is not None:
        logger.info("Owner emails are off: %s.", problem)
        return

    def once() -> None:
        with Session(get_engine()) as session:
            send_due(session, settings, clock())

    while True:
        try:
            await asyncio.to_thread(once)
        except Exception:
            logger.exception("Owner email round failed")
        await asyncio.sleep(POLL_SECONDS)
