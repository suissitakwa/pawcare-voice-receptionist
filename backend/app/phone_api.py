"""The webhook OpenAI calls when a phone call arrives."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db, get_engine
from app.errors import ApiError
from app.models import Business
from app.phone import InvalidSignature, OpenAICalls, handle_incoming, run_call, verify_signature
from app.schemas import PhoneEventReceived

logger = logging.getLogger("pawcare.phone")
router = APIRouter(prefix="/api/v1/phone", tags=["phone"])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
# Running calls, kept so they are not garbage collected and can be stopped on shutdown.
RUNNING: set[asyncio.Task] = set()


def get_calls(settings: AppSettings) -> OpenAICalls:
    return OpenAICalls(settings.openai_api_key)


def get_call_starter(settings: AppSettings):
    def start(call_id: str, session_id: uuid.UUID, business_id: uuid.UUID) -> None:
        task = asyncio.create_task(
            run_call(
                call_id,
                session_id,
                business_id,
                settings,
                get_engine,
                get_now,
                OpenAICalls(settings.openai_api_key),
            )
        )
        RUNNING.add(task)
        task.add_done_callback(RUNNING.discard)

    return start


async def stop_running_calls() -> None:
    for task in list(RUNNING):
        task.cancel()
    await asyncio.gather(*RUNNING, return_exceptions=True)


@router.post("/openai-events", response_model=PhoneEventReceived)
async def openai_events(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    calls: Annotated[OpenAICalls, Depends(get_calls)],
    start_call: Annotated[object, Depends(get_call_starter)],
    now: Annotated[datetime, Depends(get_now)],
) -> PhoneEventReceived:
    """Receives OpenAI's signed webhook. Only `realtime.call.incoming` is acted on."""
    if not settings.openai_webhook_secret:
        raise ApiError(404, "not_found", "Not found.")
    body = await request.body()
    try:
        # Freshness is judged by the real clock, never an overridden one.
        verify_signature(settings.openai_webhook_secret, request.headers, body, datetime.now(UTC))
    except InvalidSignature:
        raise ApiError(400, "invalid_signature", "The webhook signature is not valid.") from None
    try:
        event = json.loads(body)
    except ValueError:
        raise ApiError(400, "invalid_event", "The webhook body is not JSON.") from None
    if not isinstance(event, dict) or event.get("type") != "realtime.call.incoming":
        return PhoneEventReceived(status="ignored")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    def answer():
        business = db.scalar(select(Business).where(Business.slug == settings.business_slug))
        if business is None:
            logger.error("Phone call arrived but the business is not configured")
            return None
        record = handle_incoming(db, business, settings, data, calls, now)
        db.commit()
        return record

    record = await run_in_threadpool(answer)
    if record is None:
        return PhoneEventReceived(status="declined")
    start_call(data["call_id"], record.id, record.business_id)
    return PhoneEventReceived(status="answered")
