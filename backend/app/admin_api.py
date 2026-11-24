"""Admin endpoints: every sign-up across the service, and the changes an admin can make.

Every route but login needs an admin session. Each change is written to admin_actions in the
same transaction as the change itself.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import plans
from app.admin import AdminContext, authenticate, delete_account, is_protected, log_action, sign_in
from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import (
    AdminAction,
    Booking,
    Business,
    BusinessPhoneNumber,
    BusinessProfile,
    StaffUser,
    SubscriberAccount,
    SubscriberSession,
    VoiceSession,
)
from app.schemas import SLUG_PATTERN, StaffLogin
from app.subscriber_schemas import clean_email

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]
Config = Annotated[Settings, Depends(get_settings)]
MAX_ACCOUNTS = 500


def current_admin(
    db: DbSession, now: Now, authorization: Annotated[str | None, Header()] = None
) -> AdminContext:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    return authenticate(db, token, now)


Admin = Annotated[AdminContext, Depends(current_admin)]


class AdminPublic(BaseModel):
    email: str
    display_name: str


class AdminLoginResult(BaseModel):
    token: str
    expires_at: datetime
    admin: AdminPublic


class AccountSummary(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime
    last_sign_in_at: datetime | None
    locked: bool
    profile_status: str | None
    business_name: str | None
    business_slug: str | None
    plan: str | None


class StaffLoginPublic(BaseModel):
    email: str
    display_name: str


class AccountDetail(AccountSummary):
    business_type: str | None
    submitted_at: datetime | None
    business_is_protected: bool
    trial_ends_at: datetime | None
    staff_logins: list[StaffLoginPublic]
    phone_numbers: list[str]
    bookings: int
    calls: int


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=254)

    _email = field_validator("email")(clean_email)


class AccountDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Typed by the admin, so a stale screen or a wrong row can't delete an account.
    confirm_email: str = Field(max_length=254)
    delete_business: bool = True


class DeletionResult(BaseModel):
    deleted_email: str
    deleted_business_slug: str | None
    deleted_bookings: int
    released_phone_numbers: int


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["trial", "starter", "unlimited"]
    trial_days: int | None = Field(default=None, ge=1, le=365)


class ActionPublic(BaseModel):
    admin_email: str
    action: str
    target: str
    details: dict
    created_at: datetime


def to_public(admin) -> AdminPublic:
    return AdminPublic(email=admin.email, display_name=admin.display_name)


def summary_query():
    last_sign_in = (
        select(func.max(SubscriberSession.created_at))
        .where(SubscriberSession.account_id == SubscriberAccount.id)
        .scalar_subquery()
    )
    return (
        select(SubscriberAccount, BusinessProfile, Business, last_sign_in)
        .outerjoin(BusinessProfile, BusinessProfile.account_id == SubscriberAccount.id)
        .outerjoin(Business, Business.id == BusinessProfile.business_id)
    )


def to_summary(account, profile, business, last_sign_in, now: datetime) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "full_name": account.full_name,
        "created_at": account.created_at,
        "last_sign_in_at": last_sign_in,
        "locked": account.locked_until is not None and account.locked_until > now,
        "profile_status": profile.status if profile else None,
        "business_name": (business.name if business else None)
        or (profile.business_name if profile else None),
        "business_slug": business.slug if business else None,
        "plan": business.plan if business else None,
    }


def find_account(db: Session, account_id: uuid.UUID) -> SubscriberAccount:
    account = db.get(SubscriberAccount, account_id, with_for_update=True)
    if account is None:
        raise ApiError(404, "account_not_found", "No account with that id.")
    return account


def detail(db: Session, account_id: uuid.UUID, settings: Settings, now: datetime) -> AccountDetail:
    row = db.execute(summary_query().where(SubscriberAccount.id == account_id)).first()
    if row is None:
        raise ApiError(404, "account_not_found", "No account with that id.")
    account, profile, business, last_sign_in = row
    staff, numbers, bookings, calls = [], [], 0, 0
    if business is not None:
        staff = [
            StaffLoginPublic(email=s.email, display_name=s.display_name)
            for s in db.scalars(
                select(StaffUser)
                .where(StaffUser.business_id == business.id)
                .order_by(StaffUser.created_at)
            )
        ]
        numbers = list(
            db.scalars(
                select(BusinessPhoneNumber.number).where(
                    BusinessPhoneNumber.business_id == business.id
                )
            )
        )
        bookings = db.scalar(
            select(func.count()).select_from(Booking).where(Booking.business_id == business.id)
        )
        calls = db.scalar(
            select(func.count())
            .select_from(VoiceSession)
            .where(VoiceSession.business_id == business.id)
        )
    return AccountDetail(
        **to_summary(account, profile, business, last_sign_in, now),
        business_type=profile.business_type if profile else None,
        submitted_at=profile.submitted_at if profile else None,
        business_is_protected=business is not None and is_protected(business, settings),
        trial_ends_at=business.trial_ends_at if business and business.plan == "trial" else None,
        staff_logins=staff,
        phone_numbers=numbers,
        bookings=bookings,
        calls=calls,
    )


@router.post("/login", response_model=AdminLoginResult)
def login(body: StaffLogin, db: DbSession, now: Now) -> AdminLoginResult:
    token, record, admin = sign_in(db, body.email, body.password, now)
    db.commit()
    return AdminLoginResult(token=token, expires_at=record.expires_at, admin=to_public(admin))


@router.post("/logout", status_code=204)
def logout(context: Admin, db: DbSession, now: Now) -> Response:
    context.session.revoked_at = now
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=AdminPublic)
def me(context: Admin) -> AdminPublic:
    return to_public(context.admin)


@router.get("/accounts", response_model=list[AccountSummary])
def list_accounts(
    _: Admin,
    db: DbSession,
    now: Now,
    q: Annotated[str, Query(max_length=120)] = "",
) -> list[AccountSummary]:
    """Every sign-up, newest first. `q` matches email, name or business name."""
    query = (
        summary_query()
        .order_by(SubscriberAccount.created_at.desc(), SubscriberAccount.email)
        .limit(MAX_ACCOUNTS)
    )
    if q.strip():
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        query = query.where(
            or_(
                SubscriberAccount.email.ilike(like),
                SubscriberAccount.full_name.ilike(like),
                BusinessProfile.business_name.ilike(like),
                Business.name.ilike(like),
                Business.slug.ilike(like),
            )
        )
    return [AccountSummary(**to_summary(*row, now)) for row in db.execute(query).all()]


@router.get("/accounts/{account_id}", response_model=AccountDetail)
def get_account(
    account_id: uuid.UUID, _: Admin, db: DbSession, now: Now, settings: Config
) -> AccountDetail:
    return detail(db, account_id, settings, now)


@router.patch("/accounts/{account_id}", response_model=AccountDetail)
def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    context: Admin,
    db: DbSession,
    now: Now,
    settings: Config,
) -> AccountDetail:
    account = find_account(db, account_id)
    changes = {}
    if body.full_name is not None and body.full_name != account.full_name:
        changes["full_name"] = [account.full_name, body.full_name]
        account.full_name = body.full_name
    if body.email is not None and body.email != account.email:
        changes["email"] = [account.email, body.email]
        account.email = body.email
    if changes:
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, "email_taken", "Another account already uses that email.") from None
        log_action(db, context.admin, "account.updated", account.email, now, **changes)
        db.commit()
    return detail(db, account_id, settings, now)


@router.post("/accounts/{account_id}/unlock", response_model=AccountDetail)
def unlock_account(
    account_id: uuid.UUID, context: Admin, db: DbSession, now: Now, settings: Config
) -> AccountDetail:
    """Clears a lockout from too many failed sign-ins."""
    account = find_account(db, account_id)
    account.failed_attempts = 0
    account.locked_until = None
    log_action(db, context.admin, "account.unlocked", account.email, now)
    db.commit()
    return detail(db, account_id, settings, now)


@router.post("/accounts/{account_id}/sign-out", response_model=AccountDetail)
def sign_out_everywhere(
    account_id: uuid.UUID, context: Admin, db: DbSession, now: Now, settings: Config
) -> AccountDetail:
    """Ends every open session of the account, for example after a leaked password."""
    account = find_account(db, account_id)
    ended = db.execute(
        update(SubscriberSession)
        .where(
            SubscriberSession.account_id == account.id,
            SubscriberSession.revoked_at.is_(None),
            SubscriberSession.expires_at > now,
        )
        .values(revoked_at=now)
    ).rowcount
    log_action(db, context.admin, "account.signed_out", account.email, now, sessions=ended)
    db.commit()
    return detail(db, account_id, settings, now)


@router.delete("/accounts/{account_id}", response_model=DeletionResult)
def remove_account(
    account_id: uuid.UUID,
    body: AccountDeletion,
    context: Admin,
    db: DbSession,
    now: Now,
    settings: Config,
) -> DeletionResult:
    account = find_account(db, account_id)
    if body.confirm_email.strip().lower() != account.email:
        raise ApiError(
            422, "confirmation_mismatch", "Type the account's email exactly to delete it."
        )
    email = account.email
    deleted = delete_account(db, account, body.delete_business, settings)
    log_action(
        db,
        context.admin,
        "account.deleted",
        email,
        now,
        business=deleted.business_slug,
        bookings=deleted.bookings,
        phone_numbers=deleted.phone_numbers,
    )
    db.commit()
    return DeletionResult(
        deleted_email=email,
        deleted_business_slug=deleted.business_slug,
        deleted_bookings=deleted.bookings,
        released_phone_numbers=deleted.phone_numbers,
    )


@router.patch("/businesses/{slug}/plan", response_model=AccountDetail | None)
def change_plan(
    slug: Annotated[str, PathParam(min_length=1, max_length=64, pattern=SLUG_PATTERN)],
    body: PlanUpdate,
    context: Admin,
    db: DbSession,
    now: Now,
    settings: Config,
) -> AccountDetail | None:
    """Sets a business's plan, as `python -m app.plans set` does. Returns its owner's account."""
    business = db.scalar(select(Business).where(Business.slug == slug).with_for_update())
    if business is None:
        raise ApiError(404, "business_not_found", "No business with that address.")
    before = business.plan
    plans.set_plan(db, slug, body.plan, now, body.trial_days)
    log_action(
        db,
        context.admin,
        "business.plan_changed",
        slug,
        now,
        plan=[before, body.plan],
        trial_days=body.trial_days,
    )
    db.commit()
    owner = db.scalar(
        select(BusinessProfile.account_id).where(BusinessProfile.business_id == business.id)
    )
    return detail(db, owner, settings, now) if owner else None


@router.get("/actions", response_model=list[ActionPublic])
def recent_actions(_: Admin, db: DbSession) -> list[ActionPublic]:
    """The last 50 admin changes, newest first."""
    rows = db.scalars(select(AdminAction).order_by(AdminAction.created_at.desc()).limit(50))
    return [ActionPublic.model_validate(row, from_attributes=True) for row in rows]
