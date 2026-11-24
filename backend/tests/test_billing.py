"""Paying for the plan by card through Stripe: checkout, the signed webhook, the portal."""

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import billing, plans
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business
from app.phone import limit_problem
from app.staff import upsert_staff
from app.voice import MESSAGE_ONLY_PROBLEMS

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 20, 10, 0, tzinfo=LA)
PASSWORD = "correct-horse-battery"
STAFF = "/api/v1/staff"
WEBHOOK = "/api/v1/stripe/webhook"
SECRET = "whsec_test_secret"
STRIPE = Settings(
    stripe_secret_key="sk_test_key",
    stripe_webhook_secret=SECRET,
    stripe_price_id="price_monthly",
    public_site_url="https://app.example.com/",
)


@pytest.fixture(autouse=True)
def demo_on_a_trial(db: Session) -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_settings] = lambda: STRIPE
    plans.start_trial(demo(db), NOW - timedelta(days=3))
    db.commit()
    yield
    db.rollback()
    business = demo(db)
    business.plan, business.trial_ends_at, business.upgrade_requested_at = "unlimited", None, None
    business.stripe_customer_id = business.stripe_subscription_id = None
    business.billing_status = business.billing_ends_at = None
    db.commit()
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def stripe_calls(monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    def fake_post(settings: Settings, path: str, params: dict) -> dict:
        calls.append((path, params))
        return {"url": f"https://stripe.example/{path.strip('/')}"}

    monkeypatch.setattr(billing, "stripe_post", fake_post)
    return calls


# What Stripe says each subscription is now, as GET /v1/subscriptions/<id> answers. send()
# records the subscription it delivers, as Stripe's state would be after that change.
STRIPE_NOW: dict[str, dict] = {}


@pytest.fixture(autouse=True)
def stripe_answers(monkeypatch) -> Iterator[list[str]]:
    asked: list[str] = []

    def fake_get(settings: Settings, path: str) -> dict:
        asked.append(path)
        sub_id = path.removeprefix("/subscriptions/")
        return STRIPE_NOW.get(sub_id) or {"id": sub_id, "customer": "cus_1", "status": "active"}

    STRIPE_NOW.clear()
    monkeypatch.setattr(billing, "stripe_get", fake_get)
    yield asked
    STRIPE_NOW.clear()


def demo(db: Session) -> Business:
    db.expire_all()
    return db.scalar(select(Business).where(Business.slug == "pawcare-demo"))


def staff_headers(client, db: Session) -> dict:
    upsert_staff(
        db, "pawcare-demo", "maya@pawcare.example", "Maya", PasswordHasher().hash(PASSWORD)
    )
    db.commit()
    token = client.post(
        f"{STAFF}/login", json={"email": "maya@pawcare.example", "password": PASSWORD}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def signed(payload: bytes, secret: str = SECRET, at: datetime = NOW) -> dict:
    timestamp = str(int(at.timestamp()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={signature}"}


def send(client, kind: str, data: dict, current: bool = True, **sign) -> int:
    """Delivers a signed event. With current=False it's an old event: Stripe has moved on."""
    if current and kind.startswith("customer.subscription."):
        deleted = kind.endswith(".deleted")
        STRIPE_NOW[data["id"]] = {**data, "status": "canceled"} if deleted else data
    payload = json.dumps({"id": "evt_1", "type": kind, "data": {"object": data}}).encode()
    return client.post(WEBHOOK, content=payload, headers=signed(payload, **sign)).status_code


def checkout(db: Session, subscription: str = "sub_1", paid: str = "paid") -> dict:
    return {
        "mode": "subscription",
        "payment_status": paid,
        "client_reference_id": str(demo(db).id),
        "metadata": {"business_id": str(demo(db).id)},
        "customer": "cus_1",
        "subscription": subscription,
    }


def subscription(db: Session, status: str, sub_id: str = "sub_1") -> dict:
    return {
        "id": sub_id,
        "customer": "cus_1",
        "status": status,
        "metadata": {"business_id": str(demo(db).id)},
    }


# Encoding and signatures


def test_stripe_form_encoding_nests_keys() -> None:
    fields = billing.form_fields(
        {"mode": "subscription", "line_items": [{"price": "price_1", "quantity": 1}], "x": None}
    )
    assert fields == [
        ("mode", "subscription"),
        ("line_items[0][price]", "price_1"),
        ("line_items[0][quantity]", "1"),
    ]


def test_only_a_fresh_signature_with_the_secret_is_accepted() -> None:
    payload = b'{"type": "ping"}'
    good = signed(payload)["Stripe-Signature"]
    assert billing.signature_valid(payload, good, SECRET, NOW)
    assert not billing.signature_valid(payload, good, "whsec_other", NOW)
    assert not billing.signature_valid(b'{"type": "pong"}', good, SECRET, NOW)
    assert not billing.signature_valid(payload, good, SECRET, NOW + timedelta(minutes=6))
    assert not billing.signature_valid(payload, "t=abc,v1=", SECRET, NOW)
    assert not billing.signature_valid(payload, "", SECRET, NOW)


# Checkout


def test_the_dashboard_offers_card_payment_and_starts_checkout(client, db, stripe_calls) -> None:
    headers = staff_headers(client, db)
    body = client.get(f"{STAFF}/plan", headers=headers).json()
    assert (body["card_payment"], body["manage_billing"]) == (True, False)
    response = client.post(f"{STAFF}/plan/checkout", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"url": "https://stripe.example/checkout/sessions"}
    [(path, params)] = stripe_calls
    business_id = str(demo(db).id)
    assert params["mode"] == "subscription"
    assert params["line_items"] == [{"price": "price_monthly", "quantity": 1}]
    assert params["customer_email"] == "maya@pawcare.example"
    assert params["metadata"] == {"business_id": business_id}
    assert params["subscription_data"] == {"metadata": {"business_id": business_id}}
    assert params["success_url"] == "https://app.example.com/staff?billing=paid"
    assert params["cancel_url"] == "https://app.example.com/staff?billing=cancelled"
    assert client.post(f"{STAFF}/plan/checkout").status_code == 401


def test_other_businesses_return_to_their_own_dashboard(client, db, stripe_calls) -> None:
    # Staff sessions are kept per business: a business that isn't the deployment's own must
    # come back from Stripe to /b/<slug>/staff, or the owner lands on a sign-in form.
    headers = staff_headers(client, db)
    elsewhere = STRIPE.model_copy(update={"business_slug": "another-business"})
    app.dependency_overrides[get_settings] = lambda: elsewhere
    assert client.post(f"{STAFF}/plan/checkout", headers=headers).status_code == 200
    params = stripe_calls[-1][1]
    assert params["success_url"] == "https://app.example.com/b/pawcare-demo/staff?billing=paid"
    assert params["cancel_url"] == (
        "https://app.example.com/b/pawcare-demo/staff?billing=cancelled"
    )
    demo(db).stripe_customer_id = "cus_1"
    db.commit()
    client.post(f"{STAFF}/plan/portal", headers=headers)
    assert stripe_calls[-1][1]["return_url"] == "https://app.example.com/b/pawcare-demo/staff"


def test_without_stripe_owners_ask_for_an_invoice(client, db, stripe_calls) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings()
    headers = staff_headers(client, db)
    assert client.get(f"{STAFF}/plan", headers=headers).json()["card_payment"] is False
    response = client.post(f"{STAFF}/plan/checkout", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "card_payment_off"
    assert stripe_calls == []


def test_unlimited_businesses_have_nothing_to_pay(client, db, stripe_calls) -> None:
    headers = staff_headers(client, db)
    plans.set_plan(db, "pawcare-demo", "unlimited", NOW)
    db.commit()
    assert client.post(f"{STAFF}/plan/checkout", headers=headers).status_code == 409


def test_a_stripe_failure_is_a_clear_error(client, db, monkeypatch) -> None:
    def failing(settings, path, params):
        raise billing.StripeError("Stripe answered HTTP 500")

    monkeypatch.setattr(billing, "stripe_post", failing)
    response = client.post(f"{STAFF}/plan/checkout", headers=staff_headers(client, db))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "billing_unavailable"


# The webhook


def test_a_finished_checkout_puts_the_business_on_the_paid_plan(client, db, stripe_calls) -> None:
    headers = staff_headers(client, db)
    demo(db).upgrade_requested_at = NOW
    db.commit()
    assert send(client, "checkout.session.completed", checkout(db)) == 200
    # Stripe may send an event twice.
    assert send(client, "checkout.session.completed", checkout(db)) == 200
    business = demo(db)
    assert (business.plan, business.trial_ends_at, business.upgrade_requested_at) == (
        "starter",
        None,
        None,
    )
    assert (business.stripe_customer_id, business.stripe_subscription_id) == ("cus_1", "sub_1")
    body = client.get(f"{STAFF}/plan", headers=headers).json()
    assert (body["state"], body["billing_status"], body["manage_billing"]) == (
        "active",
        "active",
        True,
    )
    # Paying twice is refused; the portal manages the card instead.
    assert client.post(f"{STAFF}/plan/checkout", headers=headers).status_code == 409
    portal = client.post(f"{STAFF}/plan/portal", headers=headers)
    assert portal.json() == {"url": "https://stripe.example/billing_portal/sessions"}
    assert stripe_calls[-1][1] == {
        "customer": "cus_1",
        "return_url": "https://app.example.com/staff",
    }


def test_an_unsigned_or_stale_event_changes_nothing(client, db) -> None:
    assert send(client, "checkout.session.completed", checkout(db), secret="whsec_x") == 400
    stale = NOW - timedelta(minutes=10)
    assert send(client, "checkout.session.completed", checkout(db), at=stale) == 400
    assert client.post(WEBHOOK, content=b"{}").status_code == 400
    assert demo(db).plan == "trial"


def test_the_webhook_is_off_without_a_secret(client, db) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings()
    assert send(client, "checkout.session.completed", checkout(db)) == 404


def test_an_unpaid_checkout_waits_for_the_payment(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db, paid="unpaid"))
    assert demo(db).plan == "trial"
    assert demo(db).stripe_customer_id == "cus_1"
    send(client, "checkout.session.async_payment_succeeded", checkout(db, paid="unpaid"))
    assert demo(db).plan == "starter"


def test_the_subscription_can_arrive_before_the_checkout(client, db) -> None:
    send(client, "customer.subscription.created", subscription(db, "active"))
    business = demo(db)
    assert (business.plan, business.stripe_subscription_id) == ("starter", "sub_1")


def test_a_failed_card_keeps_working_while_stripe_retries(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db))
    send(client, "customer.subscription.updated", subscription(db, "past_due"))
    business = demo(db)
    assert business.billing_status == "past_due"
    assert plans.plan_problem(db, business, NOW) is None


def test_a_cancelled_subscription_only_takes_messages_until_paid_again(
    client, db, stripe_calls
) -> None:
    headers = staff_headers(client, db)
    send(client, "checkout.session.completed", checkout(db))
    send(client, "customer.subscription.deleted", subscription(db, "active"))
    business = demo(db)
    assert business.billing_status == "canceled"
    problem = limit_problem(db, business, Settings(), "a-caller", NOW)
    assert problem == plans.PAYMENT_STOPPED
    assert problem in MESSAGE_ONLY_PROBLEMS
    assert client.get(f"{STAFF}/plan", headers=headers).json()["state"] == "payment_stopped"
    # They can pay again, as the same Stripe customer.
    assert client.post(f"{STAFF}/plan/checkout", headers=headers).status_code == 200
    assert stripe_calls[-1][1]["customer"] == "cus_1"
    assert "customer_email" not in stripe_calls[-1][1]
    send(client, "checkout.session.completed", checkout(db, subscription="sub_2"))
    assert plans.plan_problem(db, demo(db), NOW) is None
    # A late event about the old subscription doesn't stop the new one.
    send(client, "customer.subscription.updated", subscription(db, "canceled", "sub_1"))
    business = demo(db)
    assert (business.stripe_subscription_id, business.billing_status) == ("sub_2", "active")


def test_an_unpaid_subscription_stops_too(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db))
    send(client, "customer.subscription.updated", subscription(db, "unpaid"))
    assert plans.plan_problem(db, demo(db), NOW) == plans.PAYMENT_STOPPED


def test_marking_paid_by_hand_overrides_a_stopped_subscription(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db))
    send(client, "customer.subscription.deleted", subscription(db, "canceled"))
    plans.set_plan(db, "pawcare-demo", "starter", NOW)
    db.commit()
    business = demo(db)
    assert business.billing_status is None
    assert business.stripe_customer_id == "cus_1"
    assert plans.plan_problem(db, business, NOW) is None


def test_an_unlimited_business_that_pays_stays_unlimited(client, db) -> None:
    plans.set_plan(db, "pawcare-demo", "unlimited", NOW)
    db.commit()
    send(client, "checkout.session.completed", checkout(db))
    assert demo(db).plan == "unlimited"


def test_events_for_unknown_businesses_are_ignored(client, db) -> None:
    data = {**checkout(db), "metadata": {"business_id": "not-a-uuid"}, "client_reference_id": None}
    assert send(client, "checkout.session.completed", data) == 200
    assert send(client, "invoice.paid", {"id": "in_1"}) == 200
    assert demo(db).plan == "trial"


# Cancelling in the portal: it works until the paid period ends


PAID_UNTIL = datetime(2027, 2, 20, 18, 0, tzinfo=UTC)


def test_a_scheduled_cancellation_shows_its_end_date_and_keeps_working(client, db) -> None:
    headers = staff_headers(client, db)
    send(client, "checkout.session.completed", checkout(db))
    # Newer API versions set cancel_at.
    cancelling = {**subscription(db, "active"), "cancel_at": int(PAID_UNTIL.timestamp())}
    assert send(client, "customer.subscription.updated", cancelling) == 200
    body = client.get(f"{STAFF}/plan", headers=headers).json()
    assert (body["state"], body["billing_status"]) == ("active", "active")
    assert datetime.fromisoformat(body["billing_ends_at"]) == PAID_UNTIL
    assert plans.plan_problem(db, demo(db), NOW) is None
    assert "Cancelled: ends 2027-02-20" in plans.describe(db, demo(db), NOW)
    # "Don't cancel subscription" in the portal clears it.
    send(client, "customer.subscription.updated", subscription(db, "active"))
    assert client.get(f"{STAFF}/plan", headers=headers).json()["billing_ends_at"] is None


def test_older_api_versions_give_the_period_end_instead(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db))
    on_items = {
        **subscription(db, "active"),
        "cancel_at_period_end": True,
        "items": {"data": [{"current_period_end": int(PAID_UNTIL.timestamp())}]},
    }
    send(client, "customer.subscription.updated", on_items)
    assert demo(db).billing_ends_at == PAID_UNTIL
    on_subscription = {
        **subscription(db, "active"),
        "cancel_at_period_end": True,
        "current_period_end": int((PAID_UNTIL + timedelta(days=1)).timestamp()),
    }
    send(client, "customer.subscription.updated", on_subscription)
    assert demo(db).billing_ends_at == PAID_UNTIL + timedelta(days=1)


def test_once_it_ends_the_date_goes_and_payment_has_stopped(client, db) -> None:
    send(client, "checkout.session.completed", checkout(db))
    cancelling = {**subscription(db, "active"), "cancel_at": int(PAID_UNTIL.timestamp())}
    send(client, "customer.subscription.updated", cancelling)
    send(client, "customer.subscription.deleted", cancelling)
    business = demo(db)
    assert (business.billing_status, business.billing_ends_at) == ("canceled", None)
    assert plans.plan_problem(db, business, NOW) == plans.PAYMENT_STOPPED


# Events out of order: what Stripe says now wins


def test_an_old_event_arriving_late_cannot_undo_a_newer_change(client, db, stripe_answers):
    send(client, "checkout.session.completed", checkout(db))
    cancelling = {**subscription(db, "active"), "cancel_at": int(PAID_UNTIL.timestamp())}
    send(client, "customer.subscription.updated", cancelling)
    # The earlier "renewing" event arrives last; Stripe still says it's cancelled.
    send(client, "customer.subscription.updated", subscription(db, "active"), current=False)
    assert demo(db).billing_ends_at == PAID_UNTIL
    assert stripe_answers[-1] == "/subscriptions/sub_1"


def test_a_late_checkout_after_the_subscription_ended_stays_stopped(client, db) -> None:
    STRIPE_NOW["sub_1"] = {**subscription(db, "canceled")}
    send(client, "checkout.session.completed", checkout(db))
    business = demo(db)
    assert (business.plan, business.billing_status) == ("starter", "canceled")
    assert plans.plan_problem(db, business, NOW) == plans.PAYMENT_STOPPED


def test_when_stripe_cant_be_asked_the_event_fails_and_changes_nothing(
    client, db, monkeypatch
) -> None:
    def unreachable(settings, path):
        raise billing.StripeError("Stripe can't be reached")

    monkeypatch.setattr(billing, "stripe_get", unreachable)
    # 503: Stripe retries the delivery later.
    assert send(client, "checkout.session.completed", checkout(db)) == 503
    assert send(client, "customer.subscription.updated", subscription(db, "active")) == 503
    business = demo(db)
    assert (business.plan, business.stripe_subscription_id) == ("trial", None)


def test_only_subscription_ids_are_looked_up() -> None:
    with pytest.raises(billing.StripeError, match="Not a subscription ID"):
        billing.current_subscription(STRIPE, "../customers/cus_1")
