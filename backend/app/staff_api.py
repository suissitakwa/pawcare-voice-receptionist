"""Staff-only endpoints. Every route requires a valid session and is scoped to the
signed-in staff member's business, so no request parameter can reach another business."""

import dataclasses
import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi import Path as PathParam
from sqlalchemy import func, select, true, update
from sqlalchemy.orm import Session

from app import google_calendar, plans
from app.availability import active_staff
from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import (
    BlockedPeriod,
    Booking,
    Business,
    CallbackRequest,
    Groomer,
    Service,
    SizeCategory,
    StaffUser,
    VoiceSession,
    WebhookEvent,
)
from app.owner_email import email_problem, owner_address
from app.password_reset import (
    ForgotRequest,
    ResetRequest,
    complete_reset,
    link_not_valid,
    request_reset,
)
from app.password_reset import client_hash as reset_client
from app.schemas import (
    SLUG_PATTERN,
    BlockCreated,
    BlockRequest,
    CalendarStatus,
    EmailStatus,
    PlanStatus,
    StaffBlock,
    StaffBooking,
    StaffCallback,
    StaffLogin,
    StaffLoginResult,
    StaffMetrics,
    StaffPublic,
    TeamMember,
    WebhookStatus,
    WebhookTestQueued,
)
from app.staff import StaffContext, authenticate, sign_in
from app.webhooks import booking_data, business_problem, enqueue, enqueue_notice

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])

DbSession = Annotated[Session, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]
MAX_BLOCK = timedelta(days=31)


def current_staff(
    db: DbSession, now: Now, authorization: Annotated[str | None, Header()] = None
) -> StaffContext:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    return authenticate(db, token, now)


Staff = Annotated[StaffContext, Depends(current_staff)]


def business_timezone(db: Session, business_id: uuid.UUID) -> ZoneInfo:
    return ZoneInfo(db.scalar(select(Business.timezone).where(Business.id == business_id)))


def to_public(staff: StaffUser, business: Business) -> StaffPublic:
    return StaffPublic(
        email=staff.email,
        display_name=staff.display_name,
        business_slug=business.slug,
        business_name=business.name,
        business_is_demo=business.is_demo,
        business_industry=business.industry,
    )


def booking_rows(db: Session, business_id: uuid.UUID, *conditions, order, limit: int = 200):
    timezone = business_timezone(db, business_id)
    rows = db.execute(
        select(Booking, Service.name, SizeCategory.label, Groomer.display_name)
        .join(Service, Service.id == Booking.service_id)
        .join(SizeCategory, SizeCategory.id == Booking.size_category_id)
        .join(Groomer, Groomer.id == Booking.groomer_id)
        .where(Booking.business_id == business_id, *conditions)
        .order_by(order)
        .limit(limit)
    ).all()
    return [
        to_staff_booking(booking, service, size, staff, timezone)
        for booking, service, size, staff in rows
    ]


def to_staff_booking(
    booking: Booking, service: str, size: str, staff: str, timezone: ZoneInfo
) -> StaffBooking:
    return StaffBooking(
        staff=staff,
        service_address=booking.service_address,
        reference=booking.reference,
        status=booking.status,
        service=service,
        size=size,
        pet_name=booking.pet_name,
        owner_name=booking.owner_name,
        owner_email=booking.owner_email,
        owner_phone=booking.owner_phone,
        starts_at=booking.starts_at,
        local_start=booking.starts_at.astimezone(timezone),
        local_end=booking.ends_at.astimezone(timezone),
        price_cents=booking.price_cents,
        created_at=booking.created_at,
        cancelled_at=booking.cancelled_at,
        source="voice" if booking.voice_session_id else "form",
    )


def to_staff_block(block: BlockedPeriod, staff: str | None, timezone: ZoneInfo) -> StaffBlock:
    return StaffBlock(
        id=block.id,
        starts_at=block.starts_at,
        ends_at=block.ends_at,
        local_start=block.starts_at.astimezone(timezone),
        local_end=block.ends_at.astimezone(timezone),
        reason=block.reason,
        staff=staff,
    )


def sign_in_at(
    db: Session, business: Business, request: StaffLogin, now: datetime
) -> StaffLoginResult:
    token, staff_session, staff = sign_in(db, business.id, request.email, request.password, now)
    db.commit()
    return StaffLoginResult(
        token=token, expires_at=staff_session.expires_at, staff=to_public(staff, business)
    )


@router.post("/login", response_model=StaffLoginResult)
def login(
    request: StaffLogin,
    db: DbSession,
    now: Now,
    settings: Annotated[Settings, Depends(get_settings)],
) -> StaffLoginResult:
    """Signs in to the deployment's own business (BUSINESS_SLUG)."""
    business = db.scalar(select(Business).where(Business.slug == settings.business_slug))
    if business is None:
        raise ApiError(404, "business_not_configured", "Business details have not been set up.")
    return sign_in_at(db, business, request, now)


# The same email can work at two businesses, so signing in names the business.
business_router = APIRouter(prefix="/api/v1/b/{business_slug}/staff", tags=["staff"])


def forgot_at(business: Business, body: ForgotRequest, request: Request, db, settings, now):
    staff = db.scalar(
        select(StaffUser).where(
            StaffUser.business_id == business.id, StaffUser.email == body.email.lower()
        )
    )
    link = f"/b/{business.slug}/staff"
    request_reset(db, staff, link, reset_client(request), settings, now)
    db.commit()
    return {"status": "sent_if_account_exists"}


@router.post("/password/forgot", status_code=202)
def forgot_password(
    body: ForgotRequest,
    request: Request,
    db: DbSession,
    now: Now,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """For the deployment's own business (BUSINESS_SLUG). Same answer whatever the email."""
    business = db.scalar(select(Business).where(Business.slug == settings.business_slug))
    if business is None:
        return {"status": "sent_if_account_exists"}
    return forgot_at(business, body, request, db, settings, now)


@router.post("/password/reset", status_code=204)
def reset_password(body: ResetRequest, db: DbSession, now: Now) -> Response:
    account = complete_reset(db, body, now)
    if not isinstance(account, StaffUser):
        db.rollback()
        raise link_not_valid()
    db.commit()
    return Response(status_code=204)


@business_router.post("/password/forgot", status_code=202)
def forgot_password_at_business(
    business_slug: Annotated[str, PathParam(min_length=1, max_length=64, pattern=SLUG_PATTERN)],
    body: ForgotRequest,
    request: Request,
    db: DbSession,
    now: Now,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    business = db.scalar(select(Business).where(Business.slug == business_slug))
    if business is None:
        # The same answer as for an unknown email.
        return {"status": "sent_if_account_exists"}
    return forgot_at(business, body, request, db, settings, now)


@business_router.post("/login", response_model=StaffLoginResult)
def login_to_business(
    business_slug: Annotated[str, PathParam(min_length=1, max_length=64, pattern=SLUG_PATTERN)],
    request: StaffLogin,
    db: DbSession,
    now: Now,
) -> StaffLoginResult:
    """Signs in to the business named in the address."""
    business = db.scalar(select(Business).where(Business.slug == business_slug))
    if business is None:
        raise ApiError(404, "business_not_found", "No business was found at this address.")
    return sign_in_at(db, business, request, now)


@router.post("/logout", status_code=204)
def logout(context: Staff, db: DbSession, now: Now) -> Response:
    context.session.revoked_at = now
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=StaffPublic)
def me(context: Staff, db: DbSession) -> StaffPublic:
    return to_public(context.staff, db.get(Business, context.staff.business_id))


@router.get("/bookings", response_model=list[StaffBooking])
def list_bookings(context: Staff, db: DbSession, now: Now, scope: str = "upcoming"):
    """Upcoming bookings soonest first, or past bookings most recent first."""
    business_id = context.staff.business_id
    if scope == "past":
        return booking_rows(db, business_id, Booking.ends_at <= now, order=Booking.starts_at.desc())
    if scope != "upcoming":
        raise ApiError(422, "invalid_request", "Check these fields: scope.")
    return booking_rows(db, business_id, Booking.ends_at > now, order=Booking.starts_at)


@router.post("/bookings/{reference}/cancel", response_model=StaffBooking)
def cancel_booking(reference: str, context: Staff, db: DbSession, now: Now) -> StaffBooking:
    """Cancels a booking and frees its time. Cancelling twice is harmless."""
    business_id = context.staff.business_id
    cancelled = db.execute(
        update(Booking)
        .where(
            Booking.business_id == business_id,
            Booking.reference == reference,
            Booking.status == "confirmed",
        )
        .values(
            status="cancelled",
            cancelled_at=now,
            cancelled_by=context.staff.id,
            cancelled_via="staff",
            calendar_dirty=True,
            calendar_next_attempt_at=None,
        )
        .returning(Booking.id)
    ).scalar_one_or_none()
    if cancelled is not None:
        # Only the request that actually cancelled it sends the event.
        booking = db.get(Booking, cancelled)
        business = db.get(Business, business_id)
        enqueue(db, business_id, "booking.cancelled", booking_data(booking, business), now)
    db.commit()
    rows = booking_rows(db, business_id, Booking.reference == reference, order=Booking.starts_at)
    if not rows:
        raise ApiError(404, "booking_not_found", "No booking with that reference.")
    return rows[0]


@router.get("/blocks", response_model=list[StaffBlock])
def list_blocks(context: Staff, db: DbSession, now: Now):
    business_id = context.staff.business_id
    timezone = business_timezone(db, business_id)
    blocks = db.execute(
        select(BlockedPeriod, Groomer.display_name)
        .outerjoin(Groomer, Groomer.id == BlockedPeriod.groomer_id)
        .where(BlockedPeriod.business_id == business_id, BlockedPeriod.ends_at > now)
        .order_by(BlockedPeriod.starts_at)
    ).all()
    return [to_staff_block(block, staff, timezone) for block, staff in blocks]


@router.get("/team", response_model=list[TeamMember])
def team(context: Staff, db: DbSession) -> list[TeamMember]:
    """Active staff members, who bookings and blocks can belong to."""
    return [
        TeamMember(slug=member.slug, name=member.name)
        for member in active_staff(db, context.staff.business_id)
    ]


def local_instant(day: date, wall_clock: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, wall_clock, tzinfo=timezone)


@router.post("/blocks", response_model=BlockCreated, status_code=201)
def create_block(request: BlockRequest, context: Staff, db: DbSession, now: Now) -> BlockCreated:
    """Blocks time in the business time zone. Existing bookings are kept but reported."""
    business_id = context.staff.business_id
    timezone = business_timezone(db, business_id)
    starts_at = local_instant(request.start_date, request.start_time, timezone)
    ends_at = local_instant(request.end_date, request.end_time, timezone)
    if ends_at <= starts_at:
        raise ApiError(422, "invalid_block", "The block must end after it starts.")
    if ends_at - starts_at > MAX_BLOCK:
        raise ApiError(422, "invalid_block", "A block can last at most 31 days.")
    if ends_at <= now:
        raise ApiError(422, "invalid_block", "That time has already passed.")
    member = None
    if request.staff:
        member = next((m for m in active_staff(db, business_id) if m.slug == request.staff), None)
        if member is None:
            raise ApiError(404, "unknown_staff", "That staff member is not recognised.")
    block = BlockedPeriod(
        business_id=business_id,
        groomer_id=member.id if member else None,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=request.reason,
        created_by=context.staff.id,
    )
    db.add(block)
    db.commit()
    conflicts = booking_rows(
        db,
        business_id,
        Booking.status == "confirmed",
        Booking.groomer_id == member.id if member else true(),
        Booking.starts_at < ends_at,
        Booking.ends_at > starts_at,
        order=Booking.starts_at,
    )
    return BlockCreated(
        block=to_staff_block(block, member.name if member else None, timezone),
        conflicts=conflicts,
    )


@router.delete("/blocks/{block_id}", status_code=204)
def delete_block(block_id: uuid.UUID, context: Staff, db: DbSession) -> Response:
    block = db.scalar(
        select(BlockedPeriod).where(
            BlockedPeriod.id == block_id, BlockedPeriod.business_id == context.staff.business_id
        )
    )
    if block is None:
        raise ApiError(404, "block_not_found", "No block with that id.")
    db.delete(block)
    db.commit()
    return Response(status_code=204)


@router.get("/metrics", response_model=StaffMetrics)
def metrics(context: Staff, db: DbSession, now: Now) -> StaffMetrics:
    business_id = context.staff.business_id
    week_ago = now - timedelta(days=7)

    def count(*conditions) -> int:
        return db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.business_id == business_id, *conditions)
        )

    sessions = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(VoiceSession.tool_failures), 0),
            func.count().filter(VoiceSession.channel == "phone"),
        ).where(VoiceSession.business_id == business_id, VoiceSession.started_at >= week_ago)
    ).one()
    measured = sorted(
        value
        for usage in db.scalars(
            select(VoiceSession.usage).where(
                VoiceSession.business_id == business_id,
                VoiceSession.started_at >= week_ago,
                VoiceSession.usage.is_not(None),
            )
        )
        for value in (usage or {}).get("reply_latency_ms", [])
    )
    open_callbacks = db.scalar(
        select(func.count())
        .select_from(CallbackRequest)
        .where(CallbackRequest.business_id == business_id, CallbackRequest.status == "open")
    )
    return StaffMetrics(
        upcoming_bookings=count(Booking.status == "confirmed", Booking.ends_at > now),
        bookings_last_7_days=count(Booking.created_at >= week_ago),
        cancellations_last_7_days=count(
            Booking.status == "cancelled", Booking.cancelled_at >= week_ago
        ),
        voice_bookings_last_7_days=count(
            Booking.created_at >= week_ago, Booking.voice_session_id.is_not(None)
        ),
        voice_sessions_last_7_days=sessions[0],
        phone_calls_last_7_days=sessions[2],
        open_callbacks=open_callbacks,
        tool_failures_last_7_days=sessions[1],
        voice_replies_measured=len(measured),
        voice_reply_median_ms=percentile(measured, 50),
        voice_reply_p90_ms=percentile(measured, 90),
    )


def percentile(values: list[int], pct: int) -> int | None:
    """Nearest-rank percentile of sorted values, so the result is a time that really happened."""
    if not values:
        return None
    rank = max(1, -(-pct * len(values) // 100))
    return values[rank - 1]


@router.get("/callbacks", response_model=list[StaffCallback])
def list_callbacks(context: Staff, db: DbSession, status: str = "open"):
    """Callback requests, newest first. `status` is open, done, or all."""
    if status not in ("open", "done", "all"):
        raise ApiError(422, "invalid_request", "Check these fields: status.")
    query = select(CallbackRequest).where(CallbackRequest.business_id == context.staff.business_id)
    if status != "all":
        query = query.where(CallbackRequest.status == status)
    return db.scalars(query.order_by(CallbackRequest.created_at.desc()).limit(200)).all()


@router.post("/callbacks/{callback_id}/done", response_model=StaffCallback)
def complete_callback(callback_id: uuid.UUID, context: Staff, db: DbSession, now: Now):
    """Marks a callback as handled. Repeating it is harmless."""
    callback = db.scalar(
        select(CallbackRequest).where(
            CallbackRequest.id == callback_id,
            CallbackRequest.business_id == context.staff.business_id,
        )
    )
    if callback is None:
        raise ApiError(404, "callback_not_found", "No callback with that id.")
    if callback.status == "open":
        callback.status = "done"
        callback.resolved_at = now
        callback.resolved_by = context.staff.id
        db.commit()
    return callback


@router.get("/plan", response_model=PlanStatus)
def plan_status(context: Staff, db: DbSession, now: Now) -> PlanStatus:
    """The free trial or plan, and this month's calls against its allowance."""
    business = db.get(Business, context.staff.business_id)
    return PlanStatus(**dataclasses.asdict(plans.status(db, business, now)))


@router.post("/plan/request", response_model=PlanStatus)
def request_plan(context: Staff, db: DbSession, now: Now) -> PlanStatus:
    """The owner asks to keep their receptionist on the paid plan. Nothing is charged: you're
    emailed to send an invoice, then mark it paid with python -m app.plans set."""
    business = db.get(Business, context.staff.business_id)
    if business.plan == "unlimited":
        raise ApiError(409, "no_plan_needed", "Your receptionist doesn't need a plan.")
    if business.upgrade_requested_at is None:
        business.upgrade_requested_at = now
        enqueue_notice(
            db,
            "plan.requested",
            {
                "business_name": business.name,
                "business_slug": business.slug,
                "plan": business.plan,
                "requested_by": context.staff.email,
                "contact_email": business.notify_email or business.contact_email,
                "price": plans.PRICE,
            },
            now,
        )
        db.commit()
    return PlanStatus(**dataclasses.asdict(plans.status(db, business, now)))


@router.get("/calendar", response_model=CalendarStatus)
def calendar_status(
    context: Staff, db: DbSession, settings: Annotated[Settings, Depends(get_settings)]
) -> CalendarStatus:
    """Whether the business's Google Calendar can be read and changed right now (checked with
    Google), and how syncing bookings to it is going. Never returns a key."""
    status = google_calendar.calendar_status(db, settings, context.staff.business_id)
    return CalendarStatus(**dataclasses.asdict(status))


@router.get("/emails", response_model=EmailStatus)
def email_status(
    context: Staff, db: DbSession, now: Now, settings: Annotated[Settings, Depends(get_settings)]
) -> EmailStatus:
    """Where emails about bookings and callback requests go, and whether they are sent."""
    business_id = context.staff.business_id
    week_ago = now - timedelta(days=7)
    mine = WebhookEvent.business_id == business_id
    last_error = db.execute(
        select(WebhookEvent.email_error)
        .where(mine, WebhookEvent.email_error.is_not(None), WebhookEvent.created_at >= week_ago)
        .order_by(WebhookEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return EmailStatus(
        address=owner_address(db.get(Business, business_id)),
        enabled=email_problem(settings) is None,
        sent_last_7_days=db.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(mine, WebhookEvent.email_sent_at >= week_ago)
        ),
        last_error=last_error,
    )


@router.get("/webhooks", response_model=WebhookStatus)
def webhook_status(
    context: Staff, db: DbSession, now: Now, settings: Annotated[Settings, Depends(get_settings)]
) -> WebhookStatus:
    """Whether automation events are being delivered. Never returns the URL or secret."""
    business_id = context.staff.business_id
    week_ago = now - timedelta(days=7)
    mine = WebhookEvent.business_id == business_id

    def count(*conditions) -> int:
        return db.scalar(select(func.count()).select_from(WebhookEvent).where(mine, *conditions))

    last = db.execute(
        select(WebhookEvent.delivered_at)
        .where(mine, WebhookEvent.delivered_at.is_not(None))
        .order_by(WebhookEvent.delivered_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    last_error = db.execute(
        select(WebhookEvent.last_error)
        .where(mine, WebhookEvent.last_error.is_not(None), WebhookEvent.created_at >= week_ago)
        .order_by(WebhookEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    problem = business_problem(db, settings, business_id)
    return WebhookStatus(
        configured=problem is None,
        problem=problem,
        pending=count(WebhookEvent.delivered_at.is_(None), WebhookEvent.failed_at.is_(None)),
        delivered_last_7_days=count(WebhookEvent.delivered_at >= week_ago),
        failed_last_7_days=count(WebhookEvent.failed_at >= week_ago),
        last_delivered_at=last,
        last_error=last_error,
    )


@router.post("/webhooks/test", response_model=WebhookTestQueued, status_code=202)
def send_test_event(context: Staff, db: DbSession, now: Now) -> WebhookTestQueued:
    """Queues a webhook.test event, for checking an n8n workflow end to end."""
    event = enqueue(
        db,
        context.staff.business_id,
        "webhook.test",
        {"message": "Test event from the staff dashboard.", "sent_by": context.staff.email},
        now,
    )
    db.commit()
    return WebhookTestQueued(event_id=event.id)
