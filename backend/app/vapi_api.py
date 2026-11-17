"""The one endpoint Vapi calls during a phone call."""

import json
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import Business
from app.phone_numbers import business_for_call, dialed_from_vapi
from app.vapi import (
    DECLINED_MESSAGE,
    answer_call,
    call_of,
    end_call,
    find_session,
    run_tool_calls,
    secret_matches,
)

logger = logging.getLogger("pawcare.vapi")
router = APIRouter(prefix="/api/v1/vapi", tags=["phone"])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def public_url(request: Request) -> str:
    """This endpoint's address as Vapi reaches it. Behind Railway's proxy the app sees http."""
    url = request.url
    if url.hostname not in ("localhost", "127.0.0.1", "testserver"):
        url = url.replace(scheme="https")
    return str(url.replace(query=None))


@router.post("/events")
async def vapi_events(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    now: Annotated[datetime, Depends(get_now)],
) -> dict:
    """Assistant requests, tool calls and end-of-call reports, all signed with the secret."""
    if not settings.vapi_secret:
        raise ApiError(404, "not_found", "Not found.")
    if not secret_matches(settings, request.headers):
        raise ApiError(401, "invalid_secret", "The request is not signed with the Vapi secret.")
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise ApiError(400, "invalid_event", "The body is not JSON.") from None
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        raise ApiError(400, "invalid_event", "The body has no message.")
    kind = message.get("type")
    server_url = public_url(request)

    def business_for_message() -> Business | None:
        # A call already answered stays with its business; a new call goes by the number dialed.
        call_id, _ = call_of(message)
        record = find_session(db, call_id) if call_id and kind != "assistant-request" else None
        if record is not None:
            return db.get(Business, record.business_id)
        return business_for_call(db, settings, dialed_from_vapi(message))

    def handle() -> dict:
        if kind == "end-of-call-report" or (
            kind == "status-update" and message.get("status") == "ended"
        ):
            end_call(db, message, now)
            db.commit()
            return {}
        if kind not in ("assistant-request", "tool-calls"):
            return {}
        business = business_for_message()
        if business is None:
            logger.warning("Declining a Vapi request: no business for this call")
            return {"error": DECLINED_MESSAGE} if kind == "assistant-request" else {"results": []}
        if kind == "assistant-request":
            response = answer_call(db, business, settings, message, server_url, now)
        else:
            response = run_tool_calls(db, business, settings, message, now)
        db.commit()
        return response

    return await run_in_threadpool(handle)
