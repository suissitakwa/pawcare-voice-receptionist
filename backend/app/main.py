import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.availability import compute_slots, load_day_rules, working_window
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Business, Faq, Service, ServiceSizeRule, SizeCategory
from app.schemas import (
    AvailabilityPublic,
    BusinessPublic,
    CatalogPublic,
    FaqPublic,
    LiveStatus,
    ReadyStatus,
    ServiceOptionPublic,
    ServicePublic,
    SizeCategoryPublic,
    SlotPublic,
)

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


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def get_now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def expected_revision() -> str | None:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()


app = FastAPI(title="PawCare Voice Receptionist API", version="0.1.0")

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
    business = db.scalar(
        select(Business).where(Business.id == business_id).options(selectinload(Business.hours))
    )
    size_category = db.scalar(
        select(SizeCategory).where(
            SizeCategory.business_id == business_id, SizeCategory.slug == size
        )
    )
    if size_category is None:
        raise ApiError(404, "unknown_size", "That dog size is not recognised.")
    if size_category.requires_staff_assessment:
        raise ApiError(
            422,
            "staff_assessment_required",
            "Dogs of this size need a staff assessment before booking. Please request a callback.",
        )
    rule = db.scalar(
        select(ServiceSizeRule)
        .join(Service)
        .where(
            Service.business_id == business_id,
            Service.slug == service,
            Service.is_active,
            ServiceSizeRule.size_category_id == size_category.id,
        )
    )
    if rule is None:
        raise ApiError(404, "unknown_service", "That service is not available for this dog size.")

    rules = load_day_rules(db, business, day)
    today = now.astimezone(rules.timezone).date()
    if day < today:
        raise ApiError(422, "date_in_past", "That date has already passed.")
    if day > today + timedelta(days=business.booking_horizon_days):
        raise ApiError(
            422,
            "date_too_far_ahead",
            f"Appointments can be booked up to {business.booking_horizon_days} days ahead.",
        )

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
