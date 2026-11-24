"""Card payment for the paid plan through Stripe.

- **Checkout:** the owner presses "Pay by card" on their dashboard and is sent to a Stripe
  Checkout page for the monthly subscription (STRIPE_PRICE_ID). Nothing is stored or charged
  here; card details never reach this backend.
- **Webhook:** Stripe tells this backend what happened, signed with STRIPE_WEBHOOK_SECRET. A
  finished checkout puts the business on `starter`; subscription changes keep
  `billing_status` in step. Once a subscription is cancelled, or unpaid after Stripe's
  retries, the receptionist only takes messages (app/plans.py) until it's paid again.
- **Customer portal:** "Manage billing" opens Stripe's portal for the card, invoices and
  cancelling.

Stripe's API is called over HTTPS with the standard library, as the email sender is.
"""

import hashlib
import hmac
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Business

logger = logging.getLogger("pawcare.billing")

STRIPE_API = "https://api.stripe.com/v1"
TIMEOUT_SECONDS = 15
# Stripe's own default: a signed event older than this is refused, so a captured one can't
# be replayed later.
SIGNATURE_TOLERANCE_SECONDS = 300
# Subscription statuses under which the paid plan works. past_due works while Stripe retries.
WORKING_BILLING = ("active", "trialing", "past_due")


class StripeError(Exception):
    """Stripe refused the request or couldn't be reached."""


def billing_problem(settings: Settings) -> str | None:
    """Why card payment is off, or None when it's set up."""
    missing = [
        name
        for name, value in (
            ("STRIPE_SECRET_KEY", settings.stripe_secret_key),
            ("STRIPE_WEBHOOK_SECRET", settings.stripe_webhook_secret),
            ("STRIPE_PRICE_ID", settings.stripe_price_id),
            ("PUBLIC_SITE_URL", settings.public_site_url),
        )
        if not value
    ]
    return f"{', '.join(missing)} not set" if missing else None


def form_fields(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: nested keys as a[b][0][c]."""
    fields: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            fields.extend(form_fields(value, name))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                fields.extend(form_fields({str(index): item}, name))
        elif value is not None:
            fields.append((name, str(value)))
    return fields


def stripe_post(settings: Settings, path: str, params: dict) -> dict:
    return stripe_request(settings, "POST", path, params)


def stripe_get(settings: Settings, path: str) -> dict:
    return stripe_request(settings, "GET", path)


def stripe_request(settings: Settings, method: str, path: str, params: dict | None = None):
    headers = {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "User-Agent": "receptionist-billing/1",
    }
    data = None
    if params is not None:
        data = urllib.parse.urlencode(form_fields(params)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    # Stripe's fixed https address and one of this module's paths, never one from input.
    request = urllib.request.Request(  # noqa: S310
        f"{STRIPE_API}{path}", data=data, method=method, headers=headers
    )
    try:
        # A fixed https address, never one from input.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read(500).decode("utf-8", "replace")
        raise StripeError(f"Stripe answered HTTP {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise StripeError(f"Stripe can't be reached: {error.reason}") from None


def dashboard_url(settings: Settings, business: Business, billing: str | None = None) -> str:
    """The business's own staff dashboard: /staff for the deployment's own business (the demo),
    /b/<slug>/staff for every other. Staff sessions are kept per business, so returning anywhere
    else from Stripe would show a sign-in form."""
    root = settings.public_site_url.rstrip("/")
    page = "/staff" if business.slug == settings.business_slug else f"/b/{business.slug}/staff"
    url = f"{root}{page}"
    return f"{url}?billing={billing}" if billing else url


def checkout_url(settings: Settings, business: Business, email: str) -> str:
    """A Stripe Checkout page for the monthly plan, for this business."""
    business_id = str(business.id)
    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": settings.stripe_price_id, "quantity": 1}],
        "client_reference_id": business_id,
        "metadata": {"business_id": business_id},
        "subscription_data": {"metadata": {"business_id": business_id}},
        "success_url": dashboard_url(settings, business, "paid"),
        "cancel_url": dashboard_url(settings, business, "cancelled"),
    }
    if business.stripe_customer_id:
        params["customer"] = business.stripe_customer_id
    else:
        params["customer_email"] = email
    return stripe_post(settings, "/checkout/sessions", params)["url"]


def portal_url(settings: Settings, business: Business) -> str:
    """Stripe's customer portal: the card, invoices, and cancelling."""
    params = {
        "customer": business.stripe_customer_id,
        "return_url": dashboard_url(settings, business),
    }
    return stripe_post(settings, "/billing_portal/sessions", params)["url"]


def signature_valid(payload: bytes, header: str, secret: str, now: datetime) -> bool:
    """Checks the Stripe-Signature header: t=<unix time>,v1=<HMAC-SHA256 of "t.payload">."""
    if not secret or not header:
        return False
    timestamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not timestamp.isdigit() or not signatures:
        return False
    if abs(now.timestamp() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


def business_by_id(session: Session, value: str | None) -> Business | None:
    try:
        business_id = uuid.UUID(str(value))
    except ValueError:
        return None
    return session.get(Business, business_id)


def put_on_paid_plan(business: Business, status: str) -> None:
    business.billing_status = status
    business.billing_ends_at = None
    # The demo and pilots stay unlimited even if someone pays.
    if business.plan != "unlimited":
        business.plan = "starter"
        business.trial_ends_at = None
    business.upgrade_requested_at = None


def checkout_completed(session: Session, settings: Settings, checkout: dict) -> str:
    if checkout.get("mode") != "subscription":
        return "ignored: not a subscription"
    business = business_by_id(
        session,
        (checkout.get("metadata") or {}).get("business_id") or checkout.get("client_reference_id"),
    )
    if business is None:
        return "ignored: unknown business"
    business.stripe_customer_id = checkout.get("customer") or business.stripe_customer_id
    business.stripe_subscription_id = (
        checkout.get("subscription") or business.stripe_subscription_id
    )
    if checkout.get("payment_status") not in ("paid", "no_payment_required"):
        return f"{business.slug}: checkout finished, payment pending"
    put_on_paid_plan(business, "active")
    if business.stripe_subscription_id:
        # A checkout event can arrive after later changes to its subscription.
        return subscription_changed(
            session, current_subscription(settings, business.stripe_subscription_id)
        )
    return f"{business.slug}: paid"


def scheduled_end(subscription: dict) -> datetime | None:
    """When a subscription the owner cancelled stops, or None while it renews. Newer Stripe API
    versions set cancel_at; older ones only flag cancel_at_period_end, with the period's end on
    the subscription or, since 2025, on its items."""
    when = subscription.get("cancel_at")
    if not when and subscription.get("cancel_at_period_end"):
        items = (subscription.get("items") or {}).get("data") or [{}]
        when = subscription.get("current_period_end") or items[0].get("current_period_end")
    return datetime.fromtimestamp(int(when), UTC) if when else None


SUBSCRIPTION_ID = re.compile(r"sub_[A-Za-z0-9]{1,250}")


def current_subscription(settings: Settings, subscription_id: str) -> dict:
    """The subscription as it is now. Stripe doesn't deliver events in order, so each event is
    only a sign that something changed: an older event applied late would undo a newer one."""
    if not SUBSCRIPTION_ID.fullmatch(str(subscription_id)):
        raise StripeError(f"Not a subscription ID: {subscription_id!r}")
    return stripe_get(settings, f"/subscriptions/{subscription_id}")


def subscription_changed(session: Session, subscription: dict) -> str:
    subscription_id = subscription.get("id")
    status = str(subscription.get("status") or "")
    business = session.scalar(
        select(Business).where(Business.stripe_subscription_id == subscription_id)
    )
    if business is None:
        # The subscription's events can arrive before the checkout's.
        business = business_by_id(session, (subscription.get("metadata") or {}).get("business_id"))
        if business is None:
            return "ignored: unknown business"
        if business.stripe_subscription_id and status not in WORKING_BILLING:
            # An old or abandoned subscription; the business pays through another one.
            return f"{business.slug}: ignored another subscription ({status})"
        business.stripe_subscription_id = subscription_id
    business.stripe_customer_id = subscription.get("customer") or business.stripe_customer_id
    if status in WORKING_BILLING:
        put_on_paid_plan(business, status)
        business.billing_ends_at = scheduled_end(subscription)
    else:
        business.billing_ends_at = None
        # canceled, unpaid, incomplete, incomplete_expired, paused: app/plans.py decides what
        # still works.
        business.billing_status = status
    return f"{business.slug}: subscription {status}"


SUBSCRIPTION_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
)


def handle_event(session: Session, settings: Settings, event: dict) -> str:
    """Applies one Stripe event. Subscription changes are read back from Stripe, so receiving an
    event twice, late, or out of order leaves the same result. Raises StripeError when Stripe
    can't be asked; the webhook then fails and Stripe retries it."""
    kind = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        return checkout_completed(session, settings, data)
    if kind == "checkout.session.async_payment_succeeded":
        # A bank debit that cleared after the checkout finished.
        return checkout_completed(session, settings, {**data, "payment_status": "paid"})
    if kind in SUBSCRIPTION_EVENTS:
        return subscription_changed(session, current_subscription(settings, data.get("id")))
    return f"ignored: {kind}"
