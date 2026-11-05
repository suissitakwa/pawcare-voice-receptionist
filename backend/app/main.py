import logging
import uuid
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Business, Faq, Service, ServiceSizeRule, SizeCategory
from app.schemas import (
    BusinessPublic,
    CatalogPublic,
    FaqPublic,
    LiveStatus,
    ReadyStatus,
    ServiceOptionPublic,
    ServicePublic,
    SizeCategoryPublic,
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
