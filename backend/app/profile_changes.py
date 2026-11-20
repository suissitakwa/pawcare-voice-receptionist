"""Telling us when a submitted profile changes.

The profile form saves at every step, so one editing session can mean many saves. They're
gathered into a single "profile updated" notice, sent once the owner has stopped editing
for a while. A live receptionist doesn't change by itself, so the notice is the reminder to
update the business file.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business, BusinessProfile, ProfileDocument, SubscriberAccount, WebhookEvent
from app.subscriber_schemas import (
    BUSINESS_TYPE_LABELS,
    CALENDAR_LABELS,
    SIZE_LABELS,
    TIMEZONE_LABELS,
    VOICES,
    ProfileDetails,
)
from app.webhooks import enqueue_notice

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

QUIET = timedelta(minutes=10)
MAX_FAQ_LINES = 12


def snapshot(db: Session, profile: BusinessProfile) -> dict[str, str]:
    """The profile as a person reads it, one line per section."""
    details = ProfileDetails.from_stored(profile.details)
    documents = db.scalars(
        select(ProfileDocument.filename)
        .where(ProfileDocument.profile_id == profile.id)
        .order_by(ProfileDocument.created_at)
    ).all()
    faqs = [f"Q: {f.question} A: {f.answer}" for f in details.faqs]
    if len(faqs) > MAX_FAQ_LINES:
        faqs = [*faqs[:MAX_FAQ_LINES], f"and {len(faqs) - MAX_FAQ_LINES} more"]
    calendar = CALENDAR_LABELS.get(details.calendar_provider or "", "")
    if details.calendar_provider == "other" and details.calendar_other:
        calendar = f"{calendar}: {details.calendar_other}"
    return {
        "Business name": profile.business_name or "",
        "Type": BUSINESS_TYPE_LABELS.get(profile.business_type or "", ""),
        "Phone": details.business_phone or "",
        "Website": details.website_url or "",
        "Calendar": calendar,
        "Web pages to learn from": "\n".join(details.knowledge_urls),
        "Answers to common questions": "\n".join(faqs),
        "Voice": VOICES[details.voice][0] if details.voice in VOICES else "",
        "Time zone": TIMEZONE_LABELS.get(details.timezone or "", details.timezone or ""),
        "Mobile": (
            f"Yes, ZIP codes {', '.join(details.service_zip_codes) or 'not given'}"
            + (f" ({details.service_area})" if details.service_area else "")
            if details.is_mobile
            else ""
        ),
        "Address": details.address or "",
        "Opening hours": "\n".join(
            f"{DAYS[h.weekday]} {h.opens_at:%H:%M}-{h.closes_at:%H:%M}" for h in details.hours
        ),
        "Services and prices": "\n".join(
            f"{s.name}: "
            + ", ".join(
                f"{SIZE_LABELS[p.size]} {p.duration_minutes} min ${p.price_cents / 100:.2f}"
                for p in s.prices
            )
            for s in details.services
        ),
        "Team": "\n".join(
            m.name + (f" ({', '.join(m.services)})" if m.services else "") for m in details.team
        ),
        "Documents": "\n".join(documents),
    }


def changed_sections(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    return {label: after[label] for label in after if after[label] != before.get(label)}


def note_change(
    db: Session,
    account: SubscriberAccount,
    profile: BusinessProfile,
    changes: dict[str, str],
    now: datetime,
) -> None:
    """Adds the changes to this owner's waiting notice, or starts one, and restarts the wait."""
    if profile.status != "submitted" or not changes:
        return
    pending = db.scalar(
        select(WebhookEvent)
        .where(
            WebhookEvent.event_type == "profile.updated",
            WebhookEvent.email_sent_at.is_(None),
            WebhookEvent.email_failed_at.is_(None),
            WebhookEvent.payload["data"]["owner_email"].astext == account.email,
        )
        .with_for_update()
    )
    slug = (
        db.scalar(select(Business.slug).where(Business.id == profile.business_id))
        if profile.business_id
        else None
    )
    if pending is None:
        pending = enqueue_notice(
            db,
            "profile.updated",
            {
                "owner_name": account.full_name,
                "owner_email": account.email,
                "changes": {},
            },
            now,
        )
    data = dict(pending.payload["data"])
    data.update(
        business_name=profile.business_name,
        business_slug=slug,
        # Each section shows its latest value, however many times it changed.
        changes={**data.get("changes", {}), **changes},
    )
    pending.payload = {**pending.payload, "data": data}
    pending.email_next_attempt_at = now + QUIET
