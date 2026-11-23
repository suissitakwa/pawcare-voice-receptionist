"""Emails the owner about new, changed and cancelled bookings and callback requests.

It reads the same outbox as the webhook, so an email is only sent for a change that was
saved, and a mail server being down only delays it. Each event is emailed once, retried with
growing delays, and given up after a day: an old notice does more harm than good.

The demo business never emails, unless a notification address is set for it on purpose.
"""

import asyncio
import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import go_live
from app.config import Settings
from app.format_text import format_time
from app.models import Business, WebhookEvent

logger = logging.getLogger("pawcare.email")

EMAILED_EVENTS = {
    "booking.confirmed",
    "booking.changed",
    "booking.cancelled",
    "callback.requested",
    # About the service itself, with no business yet.
    "profile.submitted",
    "profile.received",
    "profile.updated",
    "password.reset",
    "password.changed",
    # A connected calendar stopped working, or works again: to the owner and to the admin.
    "calendar.failing",
    "calendar.working",
    "calendar.alert",
}
# What a calendar problem means for booking, by the check's code.
CALENDAR_EFFECT = {
    "calendar_not_shared": "Until it's shared again, callers and your booking site can't book.",
    "calendar_read_only": "Busy times still block booking, but new bookings aren't added to it.",
    "calendar_unreachable": "While this lasts, callers and your booking site can't book.",
    "calendar_settings_unreadable": "Booking is paused until we fix it; we've been told.",
}
# Notices that go to the account they're about.
TO_THE_ACCOUNT = {
    "profile.received",
    "password.reset",
    "password.changed",
    go_live.LIVE,
    go_live.PHONE_READY,
}
MAX_ATTEMPTS = 6
FIRST_RETRY = timedelta(minutes=1)
MAX_RETRY = timedelta(hours=1)
GIVE_UP_AFTER = timedelta(hours=24)
RESET_GIVE_UP = timedelta(hours=1)
BATCH = 20
POLL_SECONDS = 10
TIMEOUT_SECONDS = 15
REASONS = {
    "health": "health question",
    "behavior": "behavior question",
    "special_handling": "special handling",
    "large_dog": "large dog",
    "booking_help": "wants an appointment",
    "other": "other",
}

Mailer = Callable[[Settings, EmailMessage], None]


RESEND_URL = "https://api.resend.com/emails"


class SendError(Exception):
    """The email service refused or couldn't be reached."""


def email_problem(settings: Settings) -> str | None:
    if not settings.smtp_host and not settings.resend_api_key:
        return "neither RESEND_API_KEY nor SMTP_HOST is set"
    if not parseaddr(settings.email_from)[1]:
        return "EMAIL_FROM is not set"
    return None


def owner_address(business: Business) -> str | None:
    if business.notify_email:
        return business.notify_email
    return None if business.is_demo else business.contact_email


def notice_address(event: WebhookEvent, settings: Settings) -> str | None:
    """Where a notice that belongs to no business goes."""
    if event.event_type in (
        "profile.submitted",
        "profile.updated",
        "calendar.alert",
        "plan.requested",
    ):
        return settings.admin_email or None
    if event.event_type in TO_THE_ACCOUNT:
        data = event.payload.get("data", {})
        return data.get("owner_email") or data.get("email")
    return None


def forget_link(event: WebhookEvent) -> None:
    """A reset link is only needed until it's emailed (or given up), never kept."""
    if event.event_type == "password.reset":
        data = dict(event.payload.get("data", {}))
        data["link"] = "(removed after sending)"
        event.payload = {**event.payload, "data": data}


def compose_notice(event: WebhookEvent, settings: Settings) -> tuple[str, str]:
    data = event.payload.get("data", {})
    business = data.get("business_name") or "a business"
    if event.event_type == "calendar.alert":
        slug = data.get("business_slug")
        if data.get("working"):
            subject = f"Calendar working again: {business}"
            lines = [f"{business} ({slug}) can use its Google Calendar again."]
        else:
            subject = f"Calendar failing: {business} ({data.get('problem_code')})"
            lines = [
                f"{business} ({slug}) can't use its Google Calendar "
                f"({data.get('calendar_id')}) since {data.get('since')}.",
                "",
                f"The owner was told: {data.get('problem')}",
                CALENDAR_EFFECT.get(data.get("problem_code"), ""),
                "",
                f"Check: python -m app.integrations show {slug}",
            ]
    elif event.event_type == "plan.requested":
        slug = data.get("business_slug")
        subject = f"Plan request: {business} wants to keep their receptionist"
        lines = [
            f"{data.get('requested_by')} asked to keep {business} ({slug}) on the "
            f"{data.get('price')} a month plan. They're on: {data.get('plan')}.",
            "",
            f"1. Send an invoice to {data.get('contact_email')}.",
            "2. Once it's paid, in Railway's backend Console:",
            f"/opt/venv/bin/python -m app.plans set {slug} starter",
        ]
    elif event.event_type == "profile.submitted":
        subject = f"New sign-up: {business} ({data.get('business_type')})"
        lines = [
            f"{data.get('owner_name')} submitted a profile for {business}.",
            "",
            f"Email: {data.get('owner_email')}",
            f"Type: {data.get('business_type')}",
            f"Phone: {data.get('business_phone') or '-'}",
            f"Website: {data.get('website') or '-'}",
            f"Calendar: {data.get('calendar') or '-'}",
            f"Web pages to learn from: {', '.join(data.get('web_pages') or []) or '-'}",
            f"Answers to common questions: {data.get('faqs', 0)}, documents: "
            f"{data.get('documents', 0)}",
            "",
            *signup_next_steps(data),
        ]
    elif event.event_type == "profile.updated":
        subject = f"Profile updated: {business}"
        lines = [f"{data.get('owner_name')} ({data.get('owner_email')}) changed their profile:", ""]
        for section, value in data.get("changes", {}).items():
            lines.append(f"{section}:")
            lines += [f"  {line}" for line in (value.splitlines() or ["(removed)"])]
            lines.append("")
        if data.get("business_slug"):
            lines.append(
                f"Their receptionist is live at /b/{data['business_slug']} and doesn't change by "
                "itself. Update their business file and load it with "
                f"python -m app.businesses load <file> --update."
            )
        else:
            lines.append("They aren't live yet. Use these when you prepare their business file.")
    elif event.event_type in (go_live.LIVE, go_live.PHONE_READY):
        subject, lines = go_live.compose(event.event_type, data, settings.service_name)
    elif event.event_type == "password.reset":
        subject = f"Reset your {settings.service_name} password"
        lines = [
            f"Hi {data.get('name')},",
            "",
            f"Someone asked to reset the password for your {settings.service_name} account "
            f"({data.get('email')}). To choose a new password, open this link within one hour:",
            "",
            data.get("link", ""),
            "",
            "The link works once. If you didn't ask for this, ignore this email: your password "
            "stays the same.",
            "",
            settings.service_name,
        ]
    elif event.event_type == "password.changed":
        subject = f"Your {settings.service_name} password was changed"
        lines = [
            f"Hi {data.get('name')},",
            "",
            f"The password for your {settings.service_name} account ({data.get('email')}) was "
            "just changed, and every other sign-in was signed out.",
            "",
            "If this was you, there's nothing to do. If it wasn't, reply to this email right away.",
            "",
            settings.service_name,
        ]
    else:
        subject = f"We received your profile for {business}"
        lines = [
            f"Hi {first_name(data.get('owner_name'))},",
            "",
            f"Thanks for setting up {business} with {settings.service_name}. We've received your "
            "profile.",
            "",
            "Next, we'll contact you within one business day to go over your services, prices "
            "and hours, connect your calendar, and set up your phone number. Your receptionist "
            "goes live after that.",
            "",
            "You can still edit your profile in the meantime. Questions? Just reply to this email.",
            "",
            settings.service_name,
        ]
    return subject, "\n".join(lines)


def first_name(full_name: str | None) -> str:
    """The greeting's name, as in the go-live emails."""
    return (str(full_name or "").split() or ["there"])[0]


def signup_next_steps(data: dict) -> list[str]:
    """What happened to the sign-up, and what's left for you."""
    approve = f"/opt/venv/bin/python -m app.provision approve {data.get('owner_email')}"
    if data.get("auto_approval") == "approved":
        slug = data.get("business_slug")
        number = data.get("number")
        return [
            f"AUTO_APPROVE_SIGNUPS is on: it's live at /b/{slug}, and they were sent the "
            "go-live emails instead of the usual receipt.",
            f"{number} rings it."
            if number
            else "No number rings it: the one above was missing, had no area code, or is "
            "registered to a business. To add one: "
            f"/opt/venv/bin/python -m app.phone_numbers add {slug} <number>",
        ]
    if data.get("auto_approval") == "failed":
        return [
            f"AUTO_APPROVE_SIGNUPS is on, but it couldn't be approved: {data.get('problem')}",
            "They were sent the usual receipt. Once it's fixed, in Railway's backend Console:",
            approve,
        ]
    return ["Next: check it with them, then in Railway's backend Console:", approve]


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
    if event.event_type == "calendar.failing":
        subject = f"Action needed: {business.name} can't use your Google Calendar"
        lines = [
            f"Your receptionist can't use your Google Calendar ({data.get('calendar_id')}).",
            "",
            data.get("problem") or "",
            CALENDAR_EFFECT.get(data.get("problem_code"), ""),
            "",
            "Once it's fixed, the receptionist picks it up by itself within 15 minutes, and we'll "
            "email you that it works again. Check it any time on the staff dashboard's Calendar "
            "section.",
        ]
    elif event.event_type == "calendar.working":
        subject = f"Your Google Calendar works again for {business.name}"
        lines = [
            f"Your receptionist can use your Google Calendar ({data.get('calendar_id')}) again. "
            "Busy times block booking and new bookings are added to it as before.",
        ]
    elif event.event_type == "callback.requested":
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
        pet = data.get("pet_name")
        who = pet or data.get("owner_name")
        subject = f"{action}: {data.get('service')} for {who}, {start}"
        contact = ", ".join(c for c in (data.get("owner_phone"), data.get("owner_email")) if c)
        lines = [
            f"{action} ({'by phone or voice' if data.get('source') == 'voice' else 'online'}).",
            "",
            f"When: {start}",
        ]
        if pet:
            lines += [
                f"Service: {data.get('service')}, {str(data.get('size', '')).lower()} dog",
                f"Dog: {pet}",
                f"Owner: {data.get('owner_name')}, {contact}",
            ]
        else:
            lines += [
                f"Service: {data.get('service')}",
                f"Client: {data.get('owner_name')}, {contact}",
            ]
        if data.get("staff"):
            lines.append(f"With: {data['staff']}")
        if data.get("service_address"):
            lines.append(f"Address: {data['service_address']}")
        lines += [
            f"Price: ${data.get('price_cents', 0) / 100:.2f}",
            f"Reference: {data.get('reference')}",
        ]
    if dashboard:
        lines += ["", f"Staff dashboard: {dashboard}"]
    lines += ["", f"Sent by {business.name}'s AI receptionist."]
    return subject, "\n".join(lines)


def message_for(event: WebhookEvent, business: Business | None, settings: Settings, to: str):
    if business is None:
        subject, body = compose_notice(event, settings)
        default_name = settings.service_name
    else:
        subject, body = compose(event, business, settings)
        default_name = f"{business.name} receptionist"
    message = EmailMessage()
    sender_name, sender = parseaddr(settings.email_from)
    message["From"] = formataddr((sender_name or default_name, sender))
    message["To"] = to
    if business is None and settings.admin_email and event.event_type in TO_THE_ACCOUNT:
        # The owner's reply reaches a person, not the notices address.
        message["Reply-To"] = settings.admin_email
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


def send_resend(settings: Settings, message: EmailMessage) -> None:
    """Sends through Resend's web API, over HTTPS, so blocked SMTP ports don't matter."""
    body = json.dumps(
        {
            "from": message["From"],
            "to": [message["To"]],
            "subject": message["Subject"],
            "text": message.get_content(),
        }
    ).encode()
    request = urllib.request.Request(
        RESEND_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "receptionist-email/1",
            # The same key on every retry, so Resend sends a given notice only once.
            "Idempotency-Key": str(message["Message-ID"]).strip("<>")[:256],
        },
    )
    try:
        # A fixed https address, never one from input.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            if response.status >= 300:
                raise SendError(f"Resend answered HTTP {response.status}")
    except urllib.error.HTTPError as error:
        detail = error.read(300).decode("utf-8", "replace")
        raise SendError(f"Resend answered HTTP {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise SendError(f"Resend can't be reached: {error.reason}") from None


def send_email(settings: Settings, message: EmailMessage) -> None:
    """Resend when it has a key, otherwise SMTP."""
    if settings.resend_api_key:
        send_resend(settings, message)
    else:
        send_smtp(settings, message)


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * (2 ** (attempts - 1)), MAX_RETRY)


def send_due(session: Session, settings: Settings, now: datetime, mail: Mailer = send_email) -> int:
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
        business = session.get(Business, event.business_id) if event.business_id else None
        to = owner_address(business) if business else notice_address(event, settings)
        if to is None:
            event.email_failed_at = now
            event.email_error = "no address to notify"
            forget_link(event)
            continue
        # A reset link stops working after an hour, so its email isn't worth sending later.
        give_up = RESET_GIVE_UP if event.event_type == "password.reset" else GIVE_UP_AFTER
        if event.created_at < now - give_up:
            event.email_failed_at = now
            event.email_error = "not sent in time"
            forget_link(event)
            continue
        try:
            mail(settings, message_for(event, business, settings, to))
        except (smtplib.SMTPException, OSError, SendError) as error:
            event.email_attempts += 1
            event.email_error = str(error)[:200]
            if event.email_attempts >= MAX_ATTEMPTS:
                event.email_failed_at = now
                forget_link(event)
                logger.warning("Owner email for event %s failed: %s", event.id, error)
            else:
                event.email_next_attempt_at = now + retry_delay(event.email_attempts)
            continue
        event.email_attempts += 1
        event.email_sent_at = now
        event.email_error = None
        forget_link(event)
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
