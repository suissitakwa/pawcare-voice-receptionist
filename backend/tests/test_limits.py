"""Daily limits: per business, overridable per business, and capped across the deployment."""

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.businesses import TEMPLATE_FILE
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, VoiceSession
from app.phone import limit_problem
from app.schemas import BusinessSeed

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 5, 10, 0, tzinfo=LA)
DEMO = "/api/v1/b/pawcare-demo/voice/sessions"
SECOND = "/api/v1/b/second-studio/voice/sessions"


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


def use(**overrides) -> None:
    settings = Settings(voice_mode="test", **overrides)
    app.dependency_overrides[get_settings] = lambda: settings


_visitor = iter(range(1, 10_000))


def start(client: TestClient, url: str) -> int:
    # A new visitor address each time, so the per-visitor limit never interferes.
    return client.post(
        url, headers={"X-Forwarded-For": f"198.51.100.{next(_visitor) % 250}"}
    ).status_code


def business(db: Session, slug: str) -> Business:
    return db.scalar(select(Business).where(Business.slug == slug))


def set_limits(db: Session, slug: str, **limits) -> None:
    db.execute(update(Business).where(Business.slug == slug).values(**limits))
    db.commit()


# Browser voice


def test_each_business_has_its_own_daily_quota(client, second_business) -> None:
    use(voice_daily_session_limit=1)
    assert start(client, DEMO) == 201
    assert start(client, DEMO) == 429
    assert start(client, SECOND) == 201
    assert start(client, SECOND) == 429


def test_a_business_can_have_its_own_limit(client, db, second_business) -> None:
    use(voice_daily_session_limit=1)
    set_limits(db, second_business, voice_daily_limit=3)
    assert [start(client, SECOND) for _ in range(4)] == [201, 201, 201, 429]
    assert [start(client, DEMO) for _ in range(2)] == [201, 429]


def test_a_limit_of_zero_turns_browser_voice_off_for_that_business(client, db, second_business):
    use()
    set_limits(db, second_business, voice_daily_limit=0)
    assert start(client, SECOND) == 429
    assert start(client, DEMO) == 201


def test_the_deployment_wide_ceiling_caps_all_businesses_together(client, second_business):
    use(voice_daily_session_limit=30, voice_daily_total_limit=2)
    assert start(client, DEMO) == 201
    assert start(client, SECOND) == 201
    response = client.post(DEMO, headers={"X-Forwarded-For": "203.0.113.200"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "voice_quota_reached"
    assert start(client, SECOND) == 429


def test_the_ceiling_counts_only_the_last_24_hours(client, second_business) -> None:
    use(voice_daily_session_limit=30, voice_daily_total_limit=1)
    assert start(client, DEMO) == 201
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=25)
    assert start(client, SECOND) == 201


# Phone


def phone_call(db: Session, slug: str, when: datetime = NOW) -> None:
    db.add(
        VoiceSession(
            business_id=business(db, slug).id,
            mode="live",
            channel="phone",
            client_hash=uuid.uuid4().hex,
            token_hash=uuid.uuid4().hex,
            started_at=when,
        )
    )
    db.commit()


def problem(db: Session, slug: str, settings: Settings) -> str | None:
    return limit_problem(db, business(db, slug), settings, "a-new-caller", NOW)


def test_phone_limits_are_per_business_and_overridable(db, second_business) -> None:
    settings = Settings(phone_daily_call_limit=1)
    phone_call(db, "pawcare-demo")
    assert problem(db, "pawcare-demo", settings) == "daily call limit reached"
    assert problem(db, second_business, settings) is None

    set_limits(db, "pawcare-demo", phone_daily_limit=5)
    assert problem(db, "pawcare-demo", settings) is None


def test_the_phone_ceiling_caps_all_businesses_together(db, second_business) -> None:
    settings = Settings(phone_daily_call_limit=50, phone_daily_total_limit=2)
    phone_call(db, "pawcare-demo")
    phone_call(db, second_business)
    assert problem(db, second_business, settings) == "deployment-wide daily call limit reached"
    phone_call(db, "pawcare-demo", NOW - timedelta(hours=30))
    assert problem(db, second_business, Settings(phone_daily_total_limit=3)) is None


# Business files


def test_a_business_file_can_set_its_limits() -> None:
    data = json.loads(TEMPLATE_FILE.read_text())
    seed = BusinessSeed.model_validate({**data, "voice_daily_limit": 60, "phone_daily_limit": 80})
    assert (seed.voice_daily_limit, seed.phone_daily_limit) == (60, 80)
    assert BusinessSeed.model_validate(data).voice_daily_limit is None
    with pytest.raises(ValidationError):
        BusinessSeed.model_validate({**data, "phone_daily_limit": -1})
