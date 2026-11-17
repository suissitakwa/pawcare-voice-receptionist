"""Staff accounts, sign-in, and sessions.

Usage:
  python -m app.staff hash        prompt for a password and print its Argon2 hash
  python -m app.staff bootstrap   create or update the staff account from settings
  python -m app.staff create <business-slug> <email> [display name]
                                  prompt for a password, then create or update that
                                  business's staff account
"""

import hashlib
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from getpass import getpass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.models import Business, StaffSession, StaffUser

SESSION_LIFETIME = timedelta(hours=8)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12

hasher = PasswordHasher()
# Verified when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "That email and password do not match.")


@dataclass(frozen=True)
class StaffContext:
    staff: StaffUser
    session: StaffSession


def sign_in(
    session: Session, business_id: uuid.UUID, email: str, password: str, now: datetime
) -> tuple[str, StaffSession, StaffUser]:
    staff = session.scalar(
        select(StaffUser)
        .where(StaffUser.business_id == business_id, StaffUser.email == email.strip().lower())
        .with_for_update()
    )
    if staff is None:
        try:
            hasher.verify(DUMMY_HASH, password)
        except VerificationError:
            pass
        raise invalid_credentials()
    if staff.locked_until is not None and staff.locked_until > now:
        raise ApiError(
            429, "too_many_attempts", "Too many failed sign-ins. Please try again later."
        )
    try:
        hasher.verify(staff.password_hash, password)
    except (VerificationError, InvalidHashError):
        staff.failed_attempts += 1
        if staff.failed_attempts >= MAX_FAILED_ATTEMPTS:
            staff.failed_attempts = 0
            staff.locked_until = now + LOCKOUT
        # Commit the count even though the request fails, or the lockout never triggers.
        session.commit()
        raise invalid_credentials() from None

    staff.failed_attempts = 0
    staff.locked_until = None
    if hasher.check_needs_rehash(staff.password_hash):
        staff.password_hash = hasher.hash(password)
    token = secrets.token_urlsafe(32)
    staff_session = StaffSession(
        staff_id=staff.id, token_hash=token_hash(token), expires_at=now + SESSION_LIFETIME
    )
    session.add(staff_session)
    session.flush()
    return token, staff_session, staff


def authenticate(session: Session, token: str, now: datetime) -> StaffContext:
    found = session.execute(
        select(StaffSession, StaffUser)
        .join(StaffUser, StaffUser.id == StaffSession.staff_id)
        .where(
            StaffSession.token_hash == token_hash(token),
            StaffSession.revoked_at.is_(None),
            StaffSession.expires_at > now,
        )
    ).first()
    if found is None:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    return StaffContext(staff=found[1], session=found[0])


def upsert_staff(
    session: Session, business_slug: str, email: str, display_name: str, password_hash: str
) -> None:
    """Creates a staff account at one business, or updates its name and password."""
    if not password_hash.startswith("$argon2"):
        raise ValueError("The password hash must be an Argon2 hash from `app.staff hash`.")
    business_id = session.scalar(select(Business.id).where(Business.slug == business_slug))
    if business_id is None:
        raise ValueError(f"No business with slug {business_slug}. Seed it first.")
    values = {"display_name": display_name, "password_hash": password_hash}
    session.execute(
        insert(StaffUser)
        .values(business_id=business_id, email=email.strip().lower(), **values)
        .on_conflict_do_update(constraint="uq_staff_users_business_email", set_=values)
    )


def bootstrap_staff(session: Session) -> bool:
    """Creates or updates the configured staff account. Returns whether one is configured."""
    settings = get_settings()
    if not settings.staff_email or not settings.staff_password_hash:
        return False
    upsert_staff(
        session,
        settings.business_slug,
        settings.staff_email,
        settings.staff_display_name,
        settings.staff_password_hash,
    )
    return True


def prompt_password() -> str:
    password = getpass("New staff password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass("Repeat it: ") != password:
        sys.exit("The passwords do not match.")
    return password


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "hash":
        print(hasher.hash(prompt_password()))
    elif command == "bootstrap":
        from app.db import get_engine

        with Session(get_engine()) as session, session.begin():
            configured = bootstrap_staff(session)
        print("Staff account updated." if configured else "No staff account configured.")
    elif command == "create" and len(sys.argv) in (4, 5):
        from app.db import get_engine

        slug, email = sys.argv[2], sys.argv[3]
        display_name = sys.argv[4] if len(sys.argv) == 5 else "Staff"
        password_hash = hasher.hash(prompt_password())
        try:
            with Session(get_engine()) as session, session.begin():
                upsert_staff(session, slug, email, display_name, password_hash)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Staff account {email.strip().lower()} saved for {slug}.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
