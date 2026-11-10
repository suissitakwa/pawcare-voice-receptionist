import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.availability import (
    check_booking_window,
    compute_slots,
    load_business,
    load_day_rules,
    resolve_option,
    working_window,
)
from app.booking import confirm_booking, prepare_booking
from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db, get_engine
from app.errors import ApiError
from app.models import Business, Faq, Service, ServiceSizeRule, SizeCategory
from app.phone_api import router as phone_router
from app.phone_api import stop_running_calls
from app.schemas import (
    AvailabilityPublic,
    BookingConfirmation,
    BusinessPublic,
    CatalogPublic,
    ConfirmBookingRequest,
    FaqPublic,
    LiveStatus,
    PrepareBookingRequest,
    PreparedBooking,
    ReadyStatus,
    ServiceOptionPublic,
    ServicePublic,
    SizeCategoryPublic,
    SlotPublic,
    VoiceEndRequest,
    VoiceSessionStarted,
    VoiceToolRequest,
    VoiceToolResult,
)
from app.staff_api import router as staff_router
from app.subscriber_api import router as subscriber_router
from app.voice import end_voice_session, start_voice_session
from app.voice_tools import run_tool
from app.webhooks import run_worker

logger = logging.getLogger("pawcare")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def describe_database_error(exc: SQLAlchemyError) -> str:
    # Connection failures carry no row data. Other driver messages can quote
    # column values, so those are logged by type to keep contact details out.
    if isinstance(exc, OperationalError):
        return f"OperationalError: {str(exc.orig).strip()}"
    return type(exc).__name__


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


class BusinessNotConfigured(Exception):
    pass


@lru_cache
def expected_revision() -> str | None:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker = None
    if settings.webhook_worker_enabled:
        worker = asyncio.create_task(run_worker(settings, get_engine, get_now))
    yield
    await stop_running_calls()
    if worker is not None:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="PawCare Voice Receptionist API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.exception_handler(SQLAlchemyError)
def handle_database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error: %s", describe_database_error(exc))
    return error_response(503, "database_unavailable", "The service is temporarily unavailable.")


@app.exception_handler(BusinessNotConfigured)
def handle_business_not_configured(_: Request, __: BusinessNotConfigured) -> JSONResponse:
    return error_response(404, "business_not_configured", "Business details have not been set up.")


@app.exception_handler(ApiError)
def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
def handle_invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Field names only: the rejected values may be anything the caller typed.
    fields = sorted({str(error["loc"][-1]) for error in exc.errors()})
    return error_response(422, "invalid_request", f"Check these fields: {', '.join(fields)}.")


@app.get("/health", response_model=LiveStatus, tags=["health"])
def liveness() -> LiveStatus:
    """The process is up. Does not touch the database."""
    return LiveStatus(status="ok")


@app.get("/health/ready", response_model=ReadyStatus, tags=["health"])
def readiness(db: DbSession) -> ReadyStatus | JSONResponse:
    """The database is reachable and migrated to the revision this build expects."""
    try:
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError as exc:
        logger.error("Readiness check failed: %s", describe_database_error(exc))
        return JSONResponse(
            status_code=503,
            content=ReadyStatus(status="unavailable", database="unreachable").model_dump(),
        )
    if revision != expected_revision():
        return JSONResponse(
            status_code=503,
            content=ReadyStatus(
                status="unavailable", database="ok", schema_revision=revision
            ).model_dump(),
        )
    return ReadyStatus(status="ready", database="ok", schema_revision=revision)


def current_business_id(db: DbSession, settings: AppSettings) -> uuid.UUID:
    business_id = db.scalar(select(Business.id).where(Business.slug == settings.business_slug))
    if business_id is None:
        raise BusinessNotConfigured
    return business_id


CurrentBusinessId = Annotated[uuid.UUID, Depends(current_business_id)]


@app.get("/api/v1/business", response_model=BusinessPublic, tags=["business"])
def get_business(db: DbSession, business_id: CurrentBusinessId) -> Business:
    """Public profile of the business this deployment serves, chosen by the server."""
    return db.scalar(
        select(Business).where(Business.id == business_id).options(selectinload(Business.hours))
    )


@app.get("/api/v1/services", response_model=CatalogPublic, tags=["business"])
def list_services(db: DbSession, business_id: CurrentBusinessId) -> CatalogPublic:
    """Active services with duration and indicative price for each eligible size."""
    sizes = db.scalars(
        select(SizeCategory)
        .where(SizeCategory.business_id == business_id)
        .order_by(SizeCategory.display_order)
    ).all()
    position = {size.id: index for index, size in enumerate(sizes)}
    services = db.scalars(
        select(Service)
        .where(Service.business_id == business_id, Service.is_active)
        .order_by(Service.display_order)
        .options(
            selectinload(Service.size_rules).options(joinedload(ServiceSizeRule.size_category))
        )
    ).all()
    return CatalogPublic(
        size_categories=[SizeCategoryPublic.model_validate(size) for size in sizes],
        services=[
            ServicePublic(
                slug=service.slug,
                name=service.name,
                description=service.description,
                options=[
                    ServiceOptionPublic(
                        size=rule.size_category.slug,
                        duration_minutes=rule.duration_minutes,
                        price_cents=rule.price_cents,
                    )
                    for rule in sorted(
                        service.size_rules, key=lambda rule: position[rule.size_category_id]
                    )
                ],
            )
            for service in services
        ],
    )


@app.get("/api/v1/faqs", response_model=list[FaqPublic], tags=["business"])
def list_faqs(db: DbSession, business_id: CurrentBusinessId) -> Sequence[Faq]:
    """Approved questions and answers. Nothing outside this list is an approved answer."""
    return db.scalars(
        select(Faq).where(Faq.business_id == business_id).order_by(Faq.display_order)
    ).all()


@app.get("/api/v1/availability", response_model=AvailabilityPublic, tags=["booking"])
def get_availability(
    db: DbSession,
    business_id: CurrentBusinessId,
    now: Annotated[datetime, Depends(get_now)],
    service: Annotated[str, Query(max_length=64)],
    size: Annotated[str, Query(max_length=32)],
    day: Annotated[date, Query(alias="date")],
) -> AvailabilityPublic:
    """Open appointment starts for one service, dog size, and local date."""
    business = load_business(db, business_id)
    _, _, rule = resolve_option(db, business_id, service, size)
    rules = load_day_rules(db, business, day)
    check_booking_window(business, day, now)

    duration = timedelta(minutes=rule.duration_minutes)
    return AvailabilityPublic(
        date=day,
        timezone=business.timezone,
        service=service,
        size=size,
        duration_minutes=rule.duration_minutes,
        closed=working_window(day, rules) is None,
        slots=[
            SlotPublic(
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                local_start=slot.starts_at.astimezone(rules.timezone),
                local_end=slot.ends_at.astimezone(rules.timezone),
            )
            for slot in compute_slots(day, duration, rules, now)
        ],
    )


Now = Annotated[datetime, Depends(get_now)]


@app.post(
    "/api/v1/bookings/prepare", response_model=PreparedBooking, status_code=201, tags=["booking"]
)
def prepare(
    request: PrepareBookingRequest, db: DbSession, business_id: CurrentBusinessId, now: Now
) -> PreparedBooking:
    """Check a slot and hold the details as a summary for the customer to confirm.

    Nothing is booked yet. The returned token and summary hash confirm exactly this summary
    for ten minutes. Changing anything means preparing again.
    """
    prepared = prepare_booking(db, load_business(db, business_id), request, now)
    db.commit()
    return prepared


@app.post("/api/v1/bookings/confirm", response_model=BookingConfirmation, tags=["booking"])
def confirm(
    request: ConfirmBookingRequest,
    response: Response,
    db: DbSession,
    business_id: CurrentBusinessId,
    now: Now,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> BookingConfirmation:
    """Book exactly the prepared summary. Retrying with the same key returns the same booking."""
    confirmation, created = confirm_booking(
        db,
        load_business(db, business_id),
        request.confirmation_token,
        request.summary_hash,
        idempotency_key,
        now,
    )
    db.commit()
    response.status_code = 201 if created else 200
    if not created:
        response.headers["Idempotent-Replayed"] = "true"
    return confirmation


app.include_router(staff_router)
app.include_router(subscriber_router)
app.include_router(phone_router)


def client_ip(request: Request) -> str:
    # Railway's proxy adds the visitor's address. The value can be forged, so the per-visitor
    # limit is best effort. The daily quota is the hard cap on cost.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "")


@app.post(
    "/api/v1/voice/sessions", response_model=VoiceSessionStarted, status_code=201, tags=["voice"]
)
def create_voice_session(
    request: Request,
    db: DbSession,
    business_id: CurrentBusinessId,
    settings: AppSettings,
    now: Now,
) -> VoiceSessionStarted:
    """Starts a voice session, returning a short-lived credential when voice is live."""
    started = start_voice_session(
        db, load_business(db, business_id), settings, client_ip(request), now
    )
    db.commit()
    return started


@app.post("/api/v1/voice/sessions/{session_id}/end", status_code=204, tags=["voice"])
def end_voice(
    session_id: uuid.UUID,
    body: VoiceEndRequest,
    db: DbSession,
    business_id: CurrentBusinessId,
    now: Now,
) -> Response:
    """Records how a session ended. Repeating it is harmless."""
    end_voice_session(db, load_business(db, business_id), session_id, body, now)
    db.commit()
    return Response(status_code=204)


@app.post(
    "/api/v1/voice/sessions/{session_id}/tools/{name}",
    response_model=VoiceToolResult,
    tags=["voice"],
)
def call_voice_tool(
    session_id: uuid.UUID,
    name: str,
    body: VoiceToolRequest,
    db: DbSession,
    business_id: CurrentBusinessId,
    settings: AppSettings,
    now: Now,
) -> VoiceToolResult:
    """Runs one assistant tool. Tool failures return ok=false so the assistant can explain."""
    outcome = run_tool(
        db, business_id, session_id, name, body.session_token, body.arguments, settings, now
    )
    db.commit()
    return outcome
