"""Admin accounts: the people who run the service, across every business.

An admin signs in at /admin to see every sign-up, correct an account, unlock it, sign it out
everywhere, change its business's plan, or delete it. Every change is logged in
admin_actions. Admin accounts are only made here, from a shell on the server:

Usage:
  python -m app.admin create <email> [display name]   prompt for a password, then create or
                                                      update that admin account
  python -m app.admin list                            every admin account
  python -m app.admin remove <email>                  delete an admin account
"""

import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import (
    AdminAction,
    AdminSession,
    AdminUser,
    Booking,
    BookingDraft,
    Business,
    BusinessPhoneNumber,
    BusinessProfile,
    SubscriberAccount,
)
from app.staff import (
    DUMMY_HASH,
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    hasher,
    invalid_credentials,
    prompt_password,
    token_hash,
)

# Shorter than a staff session: an admin can delete any account.
SESSION_LIFETIME = timedelta(hours=2)


@dataclass(frozen=True)
class AdminContext:
    admin: AdminUser
    session: AdminSession


def sign_in(
    session: Session, email: str, password: str, now: datetime
) -> tuple[str, AdminSession, AdminUser]:
    admin = session.scalar(
        select(AdminUser).where(AdminUser.email == email.strip().lower()).with_for_update()
    )
    if admin is None:
        try:
            hasher.verify(DUMMY_HASH, password)
        except VerificationError:
            pass
        raise invalid_credentials()
    if admin.locked_until is not None and admin.locked_until > now:
        raise ApiError(
            429, "too_many_attempts", "Too many failed sign-ins. Please try again later."
        )
    try:
        hasher.verify(admin.password_hash, password)
    except (VerificationError, InvalidHashError):
        admin.failed_attempts += 1
        if admin.failed_attempts >= MAX_FAILED_ATTEMPTS:
            admin.failed_attempts = 0
            admin.locked_until = now + LOCKOUT
        session.commit()
        raise invalid_credentials() from None

    admin.failed_attempts = 0
    admin.locked_until = None
    if hasher.check_needs_rehash(admin.password_hash):
        admin.password_hash = hasher.hash(password)
    token = secrets.token_urlsafe(32)
    record = AdminSession(
        admin_id=admin.id, token_hash=token_hash(token), expires_at=now + SESSION_LIFETIME
    )
    session.add(record)
    session.flush()
    return token, record, admin


def authenticate(session: Session, token: str, now: datetime) -> AdminContext:
    found = session.execute(
        select(AdminSession, AdminUser)
        .join(AdminUser, AdminUser.id == AdminSession.admin_id)
        .where(
            AdminSession.token_hash == token_hash(token),
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > now,
        )
    ).first()
    if found is None:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    return AdminContext(admin=found[1], session=found[0])


def log_action(
    session: Session,
    admin: AdminUser,
    action: str,
    target: str,
    now: datetime,
    **details: object,
) -> None:
    session.add(
        AdminAction(
            admin_id=admin.id,
            admin_email=admin.email,
            action=action,
            target=target,
            details=details,
            created_at=now,
        )
    )


def is_protected(business: Business, settings: Settings) -> bool:
    """The demo and the deployment's own business are never deleted from here."""
    return business.is_demo or business.slug == settings.business_slug


@dataclass(frozen=True)
class Deleted:
    business_slug: str | None
    bookings: int
    phone_numbers: int


def delete_account(
    session: Session,
    account: SubscriberAccount,
    delete_business: bool,
    settings: Settings,
) -> Deleted:
    """Deletes a sign-up: its sessions, profile, documents and resets go with the account.

    With delete_business, the live business made from its profile goes too, with its staff
    logins, bookings, calls and phone numbers. Without it, the business keeps running.
    """
    profile = session.scalar(
        select(BusinessProfile).where(BusinessProfile.account_id == account.id)
    )
    business = (
        session.get(Business, profile.business_id, with_for_update=True)
        if profile is not None and profile.business_id is not None
        else None
    )
    bookings = phone_numbers = 0
    slug = None
    if business is not None and delete_business:
        if is_protected(business, settings):
            raise ApiError(
                409,
                "business_protected",
                "That account's business is the demo, which can't be deleted here.",
            )
        slug = business.slug
        phone_numbers = len(
            session.scalars(
                select(BusinessPhoneNumber.id).where(BusinessPhoneNumber.business_id == business.id)
            ).all()
        )
        # Bookings hold their staff member with RESTRICT, so they go before the business
        # cascades to its staff.
        bookings = session.execute(
            delete(Booking).where(Booking.business_id == business.id)
        ).rowcount
        session.execute(delete(BookingDraft).where(BookingDraft.business_id == business.id))
        # A SQL delete, so the database's own cascades remove hours, staff, calls and
        # numbers. The ORM would instead try to empty Business.hours' foreign keys first.
        session.execute(delete(Business).where(Business.id == business.id))
    session.execute(delete(SubscriberAccount).where(SubscriberAccount.id == account.id))
    session.expire_all()
    return Deleted(business_slug=slug, bookings=bookings, phone_numbers=phone_numbers)


def upsert_admin(session: Session, email: str, display_name: str, password_hash: str) -> None:
    values = {"display_name": display_name, "password_hash": password_hash}
    session.execute(
        insert(AdminUser)
        .values(email=email.strip().lower(), **values)
        .on_conflict_do_update(index_elements=["email"], set_=values)
    )


def remove_admin(session: Session, email: str) -> bool:
    return bool(
        session.execute(delete(AdminUser).where(AdminUser.email == email.strip().lower())).rowcount
    )


def main() -> None:
    from app.db import get_engine

    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if command == "create" and len(args) in (1, 2):
        email = args[0].strip().lower()
        if "@" not in email:
            sys.exit("Give the admin's email address.")
        name = args[1] if len(args) == 2 else "Admin"
        password_hash = hasher.hash(prompt_password())
        with Session(get_engine()) as session, session.begin():
            upsert_admin(session, email, name, password_hash)
        print(f"Admin account {email} saved.")
    elif command == "list" and not args:
        with Session(get_engine()) as session:
            for admin in session.scalars(select(AdminUser).order_by(AdminUser.created_at)):
                print(f"{admin.email}\t{admin.display_name}\tsince {admin.created_at:%Y-%m-%d}")
    elif command == "remove" and len(args) == 1:
        with Session(get_engine()) as session, session.begin():
            removed = remove_admin(session, args[0])
        print("Admin account removed." if removed else "No admin account with that email.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
