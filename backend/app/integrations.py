"""Each business's own Google Calendar and automation webhook.

A business's settings are stored in `business_integrations`, with the secret (the calendar's
service account key, the webhook signing secret) encrypted by INTEGRATIONS_KEY. The
deployment's own business (BUSINESS_SLUG) falls back to the GOOGLE_* and WEBHOOK_* settings
when it has nothing stored, so a single-business deployment works unchanged.

Usage:
  python -m app.integrations generate-key
  python -m app.integrations show <business-slug>
  python -m app.integrations set-calendar <business-slug> <calendar-id> <key-file.json>
  python -m app.integrations set-webhook <business-slug> <url>     (asks for the secret)
  python -m app.integrations remove <business-slug> google_calendar|webhook
"""

import logging
import sys
import uuid
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, undefer

from app.config import Settings
from app.models import Business, BusinessIntegration

logger = logging.getLogger("pawcare.integrations")

CALENDAR = "google_calendar"
WEBHOOK = "webhook"
KINDS = (CALENDAR, WEBHOOK)


class IntegrationUnavailable(Exception):
    """Settings are stored but can't be read, for example because INTEGRATIONS_KEY is wrong."""


@dataclass(frozen=True)
class CalendarConfig:
    calendar_id: str
    service_account_json: str


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    secret: str


def cipher(settings: Settings) -> Fernet:
    if not settings.integrations_key:
        raise IntegrationUnavailable("INTEGRATIONS_KEY is not set")
    try:
        return Fernet(settings.integrations_key.encode())
    except ValueError:
        raise IntegrationUnavailable("INTEGRATIONS_KEY is not a valid key") from None


def stored(
    session: Session, settings: Settings, business_id: uuid.UUID, kind: str
) -> tuple[dict, str] | None:
    row = session.scalar(
        select(BusinessIntegration)
        .where(BusinessIntegration.business_id == business_id, BusinessIntegration.kind == kind)
        .options(undefer(BusinessIntegration.secret))
    )
    if row is None:
        return None
    try:
        secret = cipher(settings).decrypt(row.secret).decode()
    except InvalidToken:
        raise IntegrationUnavailable("the stored secret can't be decrypted") from None
    return row.config, secret


def is_deployment_business(session: Session, settings: Settings, business_id: uuid.UUID) -> bool:
    slug = session.scalar(select(Business.slug).where(Business.id == business_id))
    return slug == settings.business_slug


def calendar_config(
    session: Session, settings: Settings, business_id: uuid.UUID
) -> CalendarConfig | None:
    """The business's calendar, or None when it has none. Raises IntegrationUnavailable."""
    found = stored(session, settings, business_id, CALENDAR)
    if found is not None:
        config, secret = found
        return CalendarConfig(config["calendar_id"], secret)
    if (
        settings.google_calendar_id
        and settings.google_service_account_json
        and is_deployment_business(session, settings, business_id)
    ):
        return CalendarConfig(settings.google_calendar_id, settings.google_service_account_json)
    return None


def webhook_config(
    session: Session, settings: Settings, business_id: uuid.UUID
) -> WebhookConfig | None:
    """The business's webhook, or None when it has none. Raises IntegrationUnavailable."""
    found = stored(session, settings, business_id, WEBHOOK)
    if found is not None:
        config, secret = found
        return WebhookConfig(config["url"], secret)
    if settings.webhook_url and is_deployment_business(session, settings, business_id):
        return WebhookConfig(settings.webhook_url, settings.webhook_secret)
    return None


def businesses_with(session: Session, settings: Settings, kind: str) -> list[uuid.UUID]:
    """Businesses that have this integration stored, plus the deployment's business when the
    environment configures it."""
    ids = list(
        session.scalars(
            select(BusinessIntegration.business_id).where(BusinessIntegration.kind == kind)
        )
    )
    environment = (
        settings.google_calendar_id and settings.google_service_account_json
        if kind == CALENDAR
        else settings.webhook_url
    )
    if environment:
        own = session.scalar(select(Business.id).where(Business.slug == settings.business_slug))
        if own is not None and own not in ids:
            ids.append(own)
    return ids


def save(
    session: Session, settings: Settings, business_slug: str, kind: str, config: dict, secret: str
) -> None:
    business_id = session.scalar(select(Business.id).where(Business.slug == business_slug))
    if business_id is None:
        raise ValueError(f"No business with slug {business_slug}.")
    try:
        token = cipher(settings).encrypt(secret.encode())
    except IntegrationUnavailable as error:
        raise ValueError(f"{error}. Run `python -m app.integrations generate-key`.") from None
    session.execute(
        insert(BusinessIntegration)
        .values(business_id=business_id, kind=kind, config=config, secret=token)
        .on_conflict_do_update(
            constraint="uq_business_integrations_kind",
            set_={"config": config, "secret": token, "updated_at": func.now()},
        )
    )


def set_calendar(
    session: Session, settings: Settings, business_slug: str, calendar_id: str, key_json: str
) -> None:
    from app.google_calendar import load_service_account

    if not calendar_id.strip():
        raise ValueError("The calendar id is empty.")
    try:
        load_service_account(key_json)
    except (ValueError, KeyError) as error:
        raise ValueError(f"That isn't a service account key: {error}") from None
    save(session, settings, business_slug, CALENDAR, {"calendar_id": calendar_id.strip()}, key_json)


def set_webhook(
    session: Session, settings: Settings, business_slug: str, url: str, secret: str
) -> None:
    from app.webhooks import target_problem

    problem = target_problem(url, secret)
    if problem is not None:
        raise ValueError(f"Can't use this webhook: {problem}.")
    save(session, settings, business_slug, WEBHOOK, {"url": url}, secret)


def remove(session: Session, business_slug: str, kind: str) -> bool:
    business_id = session.scalar(select(Business.id).where(Business.slug == business_slug))
    if business_id is None or kind not in KINDS:
        return False
    removed = session.execute(
        delete(BusinessIntegration).where(
            BusinessIntegration.business_id == business_id, BusinessIntegration.kind == kind
        )
    )
    return removed.rowcount > 0


def describe(session: Session, business_slug: str) -> list[str]:
    """What a business has connected, without any secret."""
    rows = session.execute(
        select(BusinessIntegration.kind, BusinessIntegration.config, BusinessIntegration.updated_at)
        .join(Business, Business.id == BusinessIntegration.business_id)
        .where(Business.slug == business_slug)
        .order_by(BusinessIntegration.kind)
    ).all()
    lines = []
    for kind, config, updated_at in rows:
        detail = config.get("calendar_id") if kind == CALENDAR else "URL set"
        lines.append(f"{kind}: {detail} (updated {updated_at:%Y-%m-%d})")
    return lines


def main() -> None:
    from app.config import get_settings
    from app.db import get_engine

    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if command == "generate-key" and not args:
        print(Fernet.generate_key().decode())
        return
    settings = get_settings()
    with Session(get_engine()) as session, session.begin():
        try:
            if command == "show" and len(args) == 1:
                print("\n".join(describe(session, args[0])) or "Nothing stored.")
            elif command == "set-calendar" and len(args) == 3:
                set_calendar(session, settings, args[0], args[1], Path(args[2]).read_text())
                print(f"Calendar saved for {args[0]}.")
            elif command == "set-webhook" and len(args) == 2:
                secret = getpass("Webhook signing secret (at least 32 characters): ")
                set_webhook(session, settings, args[0], args[1], secret)
                print(f"Webhook saved for {args[0]}.")
            elif command == "remove" and len(args) == 2:
                print("Removed." if remove(session, args[0], args[1]) else "Nothing to remove.")
            else:
                sys.exit(__doc__)
        except (ValueError, OSError) as error:
            sys.exit(str(error))


if __name__ == "__main__":
    main()
