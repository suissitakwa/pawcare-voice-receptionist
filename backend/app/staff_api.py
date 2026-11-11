"""Staff-only endpoints. Every route requires a valid session and is scoped to the
signed-in staff member's business, so no request parameter can reach another business."""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

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
    VoiceSession,
    WebhookEvent,
)
from app.schemas import (
    BlockCreated,
    BlockRequest,
    StaffBlock,
    StaffBooking,
    StaffCallback,
    StaffLogin,
    StaffLoginResult,
    StaffMetrics,
    StaffPublic,
    WebhookStatus,
    WebhookTestQueued,
)
from app.staff import StaffContext, authenticate, sign_in
from app.webhooks import booking_data, configuration_problem, enqueue

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


def to_public(context_staff) -> StaffPublic:
    return StaffPublic(email=context_staff.email, display_name=context_staff.display_name)


def booking_rows(db: Session, business_id: uuid.UUID, *conditions, order, limit: int = 200):
    timezone = business_timezone(db, business_id)
    rows = db.execute(
        select(Booking, Service.name, SizeCategory.label)
        .join(Service, Service.id == Booking.service_id)
        .join(SizeCategory, SizeCategory.id == Booking.size_category_id)
        .where(Booking.business_id == business_id, *conditions)
        .order_by(order)
        .limit(limit)
    ).all()
    return [to_staff_booking(booking, service, size, timezone) for booking, service, size in rows]


def to_staff_booking(booking: Booking, service: str, size: str, timezone: ZoneInfo) -> StaffBooking:
    return StaffBooking(
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


def to_staff_block(block: BlockedPeriod, timezone: ZoneInfo) -> StaffBlock:
    return StaffBlock(
        id=block.id,
        starts_at=block.starts_at,
        ends_at=block.ends_at,
        local_start=block.starts_at.astimezone(timezone),
        local_end=block.ends_at.astimezone(timezone),
        reason=block.reason,
    )


@router.post("/login", response_model=StaffLoginResult)
def login(
    request: StaffLogin,
    db: DbSession,
    now: Now,
    settings: Annotated[Settings, Depends(get_settings)],
) -> StaffLoginResult:
    business_id = db.scalar(select(Business.id).where(Business.slug == settings.business_slug))
    if business_id is None:
        raise ApiError(404, "business_not_configured", "Business details have not been set up.")
    token, staff_session, staff = sign_in(db, business_id, request.email, request.password, now)
    db.commit()
    return StaffLoginResult(
        token=token, expires_at=staff_session.expires_at, staff=to_public(staff)
    )


@router.post("/logout", status_code=204)
def logout(context: Staff, db: DbSession, now: Now) -> Response:
    context.session.revoked_at = now
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=StaffPublic)
def me(context: Staff) -> StaffPublic:
    return to_public(context.staff)


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
    blocks = db.scalars(
        select(BlockedPeriod)
        .join(Groomer, Groomer.id == BlockedPeriod.groomer_id)
        .where(Groomer.business_id == business_id, BlockedPeriod.ends_at > now)
        .order_by(BlockedPeriod.starts_at)
    ).all()
    return [to_staff_block(block, timezone) for block in blocks]


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
    groomer_id = db.scalar(
        select(Groomer.id)
        .where(Groomer.business_id == business_id, Groomer.is_active)
        .order_by(Groomer.slug)
        .limit(1)
    )
    if groomer_id is None:
        raise ApiError(409, "no_groomer", "There is no active groomer to block.")
    block = BlockedPeriod(
        groomer_id=groomer_id,
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
        Booking.groomer_id == groomer_id,
        Booking.starts_at < ends_at,
        Booking.ends_at > starts_at,
        order=Booking.starts_at,
    )
    return BlockCreated(block=to_staff_block(block, timezone), conflicts=conflicts)


@router.delete("/blocks/{block_id}", status_code=204)
def delete_block(block_id: uuid.UUID, context: Staff, db: DbSession) -> Response:
    block = db.scalar(
        select(BlockedPeriod)
        .join(Groomer, Groomer.id == BlockedPeriod.groomer_id)
        .where(BlockedPeriod.id == block_id, Groomer.business_id == context.staff.business_id)
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
    problem = configuration_problem(settings)
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
