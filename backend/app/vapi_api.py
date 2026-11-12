"""The one endpoint Vapi calls during a phone call."""

import json
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ApiError
from app.fish_voice import VAPI_RATES, FishError, fish_rate, open_fish, pcm_chunks
from app.models import Business
from app.vapi import answer_call, end_call, run_tool_calls, secret_matches

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


def get_fish_opener():
    return open_fish


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

    def handle() -> dict:
        business = db.scalar(select(Business).where(Business.slug == settings.business_slug))
        if business is None:
            logger.error("Vapi request arrived but the business is not configured")
            raise ApiError(503, "business_not_configured", "The business is not configured.")
        if kind == "assistant-request":
            response = answer_call(db, business, settings, message, server_url, now)
        elif kind == "tool-calls":
            response = run_tool_calls(db, business, settings, message, now)
        elif kind == "end-of-call-report" or (
            kind == "status-update" and message.get("status") == "ended"
        ):
            end_call(db, message, now)
            response = {}
        else:
            response = {}
        db.commit()
        return response

    return await run_in_threadpool(handle)


@router.post("/voice", response_class=StreamingResponse)
async def vapi_voice(
    request: Request,
    settings: AppSettings,
    opener: Annotated[object, Depends(get_fish_opener)],
) -> StreamingResponse:
    """Vapi's custom voice: one sentence in, raw 16-bit mono PCM out, from Fish Audio."""
    if not settings.vapi_secret or settings.vapi_voice_provider != "fish":
        raise ApiError(404, "not_found", "Not found.")
    if not secret_matches(settings, request.headers):
        raise ApiError(401, "invalid_secret", "The request is not signed with the Vapi secret.")
    if not settings.fish_api_key or not settings.fish_voice_id:
        logger.error("VAPI_VOICE_PROVIDER is fish but FISH_API_KEY or FISH_VOICE_ID is missing")
        raise ApiError(503, "voice_unavailable", "The voice is not configured.")
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise ApiError(400, "invalid_event", "The body is not JSON.") from None
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict) or message.get("type") != "voice-request":
        raise ApiError(400, "invalid_event", "Expected a voice-request.")
    text = message.get("text")
    rate = message.get("sampleRate")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ApiError(400, "invalid_text", "The text is missing or too long.")
    if rate not in VAPI_RATES:
        raise ApiError(400, "invalid_sample_rate", "That sample rate is not supported.")
    ask_rate, step = fish_rate(rate)
    try:
        # Opened before answering, so a refusal becomes an error status, not silence.
        stream = await run_in_threadpool(opener, settings, text, ask_rate)
    except FishError as error:
        logger.error("Fish Audio failed: %s", error)
        raise ApiError(502, "voice_unavailable", "The voice could not be generated.") from None
    return StreamingResponse(pcm_chunks(stream, step), media_type="application/octet-stream")
