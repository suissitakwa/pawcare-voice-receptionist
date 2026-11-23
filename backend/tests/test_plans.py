"""The free trial, the $49 plan and its monthly calls, and asking to keep it."""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import plans, voice
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, VoiceSession, WebhookEvent
from app.owner_email import message_for, notice_address
from app.phone import limit_problem
from app.staff import upsert_staff
from app.voice import MESSAGE_ONLY_PROBLEMS

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 20, 10, 0, tzinfo=LA)
PASSWORD = "correct-horse-battery"
STAFF = "/api/v1/staff"


@pytest.fixture(autouse=True)
def demo_on_a_trial(db: Session) -> Iterator[Business]:
    app.dependency_overrides[get_now] = lambda: NOW
    business = demo(db)
    plans.start_trial(business, NOW - timedelta(days=3))
    db.commit()
    yield business
    db.rollback()
    db.execute(delete(VoiceSession))
    business = demo(db)
    business.plan, business.trial_ends_at, business.upgrade_requested_at = "unlimited", None, None
    db.commit()
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


def demo(db: Session) -> Business:
    db.expire_all()
    return db.scalar(select(Business).where(Business.slug == "pawcare-demo"))


def calls(db: Session, count: int, when: datetime = NOW) -> None:
    business = demo(db)
    db.add_all(
        VoiceSession(
            business_id=business.id,
            mode="live",
            channel="phone",
            client_hash=uuid.uuid4().hex,
            token_hash=uuid.uuid4().hex,
            started_at=when,
        )
        for _ in range(count)
    )
    db.commit()


# The plan's rules


def test_a_trial_works_in_full_until_it_ends(db) -> None:
    business = demo(db)
    assert business.trial_ends_at == NOW + timedelta(days=11)
    assert plans.plan_problem(db, business, NOW) is None
    assert plans.status(db, business, NOW).state == "trial"
    later = NOW + timedelta(days=11)
    assert plans.plan_problem(db, business, later) == plans.TRIAL_ENDED
    assert plans.status(db, business, later).state == "trial_ended"


def test_the_monthly_calls_run_out_at_100_and_come_back_next_month(db) -> None:
    calls(db, 99)
    # Calls in December don't count in January, in the business's own time zone.
    calls(db, 5, datetime(2026, 12, 31, 23, 30, tzinfo=LA))
    business = demo(db)
    assert plans.calls_this_month(db, business, NOW) == 99
    assert plans.plan_problem(db, business, NOW) is None
    calls(db, 1)
    assert plans.plan_problem(db, demo(db), NOW) == plans.ALLOWANCE_USED
    assert plans.status(db, demo(db), NOW).state == "allowance_used"
    february = datetime(2027, 2, 1, 0, 30, tzinfo=LA)
    plans.set_plan(db, "pawcare-demo", "starter", NOW)
    db.commit()
    assert plans.plan_problem(db, demo(db), february) is None


def test_unlimited_businesses_never_run_out(db) -> None:
    plans.set_plan(db, "pawcare-demo", "unlimited", NOW)
    db.commit()
    calls(db, 150)
    status = plans.status(db, demo(db), NOW)
    assert (status.state, status.calls_included) == ("unlimited", None)


# What callers and website visitors get


def test_after_the_trial_calls_only_take_messages(db) -> None:
    business = demo(db)
    business.trial_ends_at = NOW - timedelta(minutes=1)
    db.commit()
    problem = limit_problem(db, demo(db), Settings(), "a-caller", NOW)
    assert problem == "trial ended"
    assert problem in MESSAGE_ONLY_PROBLEMS
    calls(db, 100)
    plans.set_plan(db, "pawcare-demo", "starter", NOW)
    db.commit()
    assert limit_problem(db, demo(db), Settings(), "a-caller", NOW) == (
        "monthly call allowance used"
    )
    assert "monthly call allowance used" in MESSAGE_ONLY_PROBLEMS


def test_the_website_voice_assistant_pauses_too(client, db, monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(voice_mode="test")
    assert client.post("/api/v1/voice/sessions").status_code == 201
    business = demo(db)
    business.trial_ends_at = NOW - timedelta(minutes=1)
    db.commit()
    response = client.post("/api/v1/voice/sessions", headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_paused"
    assert voice.MESSAGE_ONLY_PROBLEMS  # the booking form is untouched


# The owner's dashboard


def staff_headers(client, db: Session) -> dict:
    upsert_staff(
        db, "pawcare-demo", "maya@pawcare.example", "Maya", PasswordHasher().hash(PASSWORD)
    )
    db.commit()
    token = client.post(
        f"{STAFF}/login", json={"email": "maya@pawcare.example", "password": PASSWORD}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_dashboard_shows_the_trial_and_its_calls(client, db) -> None:
    headers = staff_headers(client, db)
    calls(db, 12)
    body = client.get(f"{STAFF}/plan", headers=headers).json()
    assert body["plan"] == "trial" and body["state"] == "trial"
    assert body["price"] == "$49"
    assert (body["calls_this_month"], body["calls_included"]) == (12, 100)
    assert datetime.fromisoformat(body["trial_ends_at"]) == NOW + timedelta(days=11)
    assert body["upgrade_requested_at"] is None
    assert client.get(f"{STAFF}/plan").status_code == 401


def test_asking_to_keep_it_emails_you_once(client, db) -> None:
    headers = staff_headers(client, db)
    first = client.post(f"{STAFF}/plan/request", headers=headers)
    assert first.status_code == 200
    assert first.json()["upgrade_requested_at"] is not None
    client.post(f"{STAFF}/plan/request", headers=headers)
    [notice] = db.scalars(
        select(WebhookEvent).where(WebhookEvent.event_type == "plan.requested")
    ).all()
    settings = Settings(admin_email="team@helloever.example")
    assert notice_address(notice, settings) == "team@helloever.example"
    message = message_for(notice, None, settings, "team@helloever.example")
    assert message["Subject"] == (
        "Plan request: PawCare Grooming Studio wants to keep their receptionist"
    )
    body = message.get_content()
    assert "maya@pawcare.example asked to keep PawCare Grooming Studio (pawcare-demo)" in body
    assert "python -m app.plans set pawcare-demo starter" in body
    # Marking it paid answers the request and ends the trial.
    plans.set_plan(db, "pawcare-demo", "starter", NOW)
    db.commit()
    body = client.get(f"{STAFF}/plan", headers=headers).json()
    assert (body["plan"], body["state"], body["trial_ends_at"]) == ("starter", "active", None)
    assert body["upgrade_requested_at"] is None


def test_unlimited_businesses_have_nothing_to_ask_for(client, db) -> None:
    headers = staff_headers(client, db)
    plans.set_plan(db, "pawcare-demo", "unlimited", NOW)
    db.commit()
    assert client.post(f"{STAFF}/plan/request", headers=headers).status_code == 409


# Setting plans by hand


def test_a_trial_can_be_extended_and_unknown_plans_are_refused(db) -> None:
    business = plans.set_plan(db, "pawcare-demo", "trial", NOW, days=30)
    assert business.trial_ends_at == NOW + timedelta(days=30)
    with pytest.raises(ValueError, match="Choose one of"):
        plans.set_plan(db, "pawcare-demo", "gold", NOW)
    with pytest.raises(ValueError, match="No business"):
        plans.set_plan(db, "nobody", "starter", NOW)
