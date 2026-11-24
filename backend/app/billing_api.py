"""The endpoint Stripe calls about payments and subscriptions (app/billing.py)."""

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.billing import StripeError, handle_event, logger, signature_valid
from app.clock import get_now
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ApiError

router = APIRouter(prefix="/api/v1/stripe", tags=["billing"])


def apply_event(db: Session, settings: Settings, event: dict) -> str:
    try:
        outcome = handle_event(db, settings, event)
    except StripeError as error:
        db.rollback()
        logger.warning("Stripe %s %s not applied: %s", event.get("type"), event.get("id"), error)
        # Stripe retries a failed delivery, so nothing is lost.
        raise ApiError(503, "billing_unavailable", "Stripe couldn't be reached.") from None
    db.commit()
    return outcome


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_now)],
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict:
    """Signed with STRIPE_WEBHOOK_SECRET. Anything unsigned, or signed too long ago, is
    refused; Stripe retries a refused or failed event for days."""
    if not settings.stripe_webhook_secret:
        raise ApiError(404, "not_found", "Not found.")
    payload = await request.body()
    if not signature_valid(payload, stripe_signature or "", settings.stripe_webhook_secret, now):
        raise ApiError(400, "bad_signature", "The Stripe signature doesn't match.")
    try:
        event = json.loads(payload)
    except ValueError:
        raise ApiError(400, "bad_event", "The event isn't JSON.") from None
    outcome = await run_in_threadpool(apply_event, db, settings, event)
    logger.info("Stripe %s %s: %s", event.get("type"), event.get("id"), outcome)
    return {"received": True}
