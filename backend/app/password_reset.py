"""Forgotten passwords, for sign-up accounts and staff dashboard logins.

Asking always gets the same answer, whether or not the email has an account, so the form
can't be used to find out who has one. A link is emailed only to an existing account: it
works once, for an hour, and only its hash is stored. Choosing a new password signs out
every other session and emails the account that its password changed.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import (
    PasswordReset,
    StaffSession,
    StaffUser,
    SubscriberAccount,
    SubscriberSession,
)
from app.staff import hasher
from app.webhooks import enqueue_notice

logger = logging.getLogger("pawcare.password")

RESET_LIFETIME = timedelta(hours=1)
# Enough for someone who mistyped or lost an email, not enough to flood an inbox.
PER_ACCOUNT_PER_HOUR = 3
PER_CLIENT_PER_HOUR = 10
MIN_PASSWORD_LENGTH = 12

Account = SubscriberAccount | StaffUser


class ForgotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def client_hash(request) -> str:
    """The visitor, as a hash of their address, for the per-visitor limit."""
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    return sha256(f"password-reset:{address}")


def owner_column(account: Account):
    return (
        PasswordReset.subscriber_account_id
        if isinstance(account, SubscriberAccount)
        else PasswordReset.staff_user_id
    )


def display_name(account: Account) -> str:
    return account.full_name if isinstance(account, SubscriberAccount) else account.display_name


def recent(session: Session, condition, now: datetime) -> int:
    return session.scalar(
        select(func.count())
        .select_from(PasswordReset)
        .where(condition, PasswordReset.created_at > now - timedelta(hours=1))
    )


def request_reset(
    session: Session,
    account: Account | None,
    link_base: str,
    client: str,
    settings: Settings,
    now: datetime,
) -> None:
    """Emails a link if the account exists and limits allow. Never says which happened."""
    if not settings.public_site_url:
        logger.error("PUBLIC_SITE_URL is not set, so no password reset link can be sent")
        return
    if recent(session, PasswordReset.client_hash == client, now) >= PER_CLIENT_PER_HOUR:
        return
    if account is None:
        # Nothing to send. The caller answers exactly as if a link went out.
        return
    column = owner_column(account)
    if recent(session, column == account.id, now) >= PER_ACCOUNT_PER_HOUR:
        return
    # Only the newest link works.
    session.execute(
        update(PasswordReset)
        .where(column == account.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            subscriber_account_id=account.id if isinstance(account, SubscriberAccount) else None,
            staff_user_id=account.id if isinstance(account, StaffUser) else None,
            token_hash=sha256(token),
            client_hash=client,
            created_at=now,
            expires_at=now + RESET_LIFETIME,
        )
    )
    enqueue_notice(
        session,
        "password.reset",
        {
            "email": account.email,
            "name": display_name(account),
            "link": f"{settings.public_site_url.rstrip('/')}{link_base}?reset={token}",
        },
        now,
    )


def link_not_valid() -> ApiError:
    return ApiError(
        400,
        "reset_link_invalid",
        "This link has expired or was already used. Ask for a new one from the sign-in page.",
    )


def complete_reset(session: Session, request: ResetRequest, now: datetime) -> Account:
    """Sets the new password from a valid link and signs out every session."""
    reset = session.scalar(
        select(PasswordReset)
        .where(PasswordReset.token_hash == sha256(request.token))
        .with_for_update()
    )
    if reset is None or reset.used_at is not None or reset.expires_at <= now:
        raise link_not_valid()
    reset.used_at = now
    if reset.subscriber_account_id is not None:
        account: Account = session.get(SubscriberAccount, reset.subscriber_account_id)
        session.execute(
            update(SubscriberSession)
            .where(
                SubscriberSession.account_id == account.id,
                SubscriberSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    else:
        account = session.get(StaffUser, reset.staff_user_id)
        session.execute(
            update(StaffSession)
            .where(StaffSession.staff_id == account.id, StaffSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    account.password_hash = hasher.hash(request.password)
    account.failed_attempts = 0
    account.locked_until = None
    enqueue_notice(
        session,
        "password.changed",
        {"email": account.email, "name": display_name(account)},
        now,
    )
    return account
