"""Voice sessions without the provider: OpenAI is replaced, so these tests cost nothing."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import voice
from app.config import Settings, get_settings
from app.main import app, get_now
from app.models import Business, VoiceSession
from app.voice import build_instructions

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 5, 10, 0, tzinfo=LA)
START = "/api/v1/voice/sessions"
FAKE_KEY = "fake-openai-key-for-tests"


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)
    app.dependency_overrides.pop(get_settings, None)


def use_settings(**overrides) -> Settings:
    settings = Settings(**overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


@pytest.fixture
def minted(monkeypatch) -> list[dict]:
    """Replaces the OpenAI call and records what would have been sent."""
    calls: list[dict] = []

    def fake_mint(settings, instructions, safety_id):
        calls.append(
            {"key": settings.openai_api_key, "instructions": instructions, "id": safety_id}
        )
        return "ek_test_secret", int((NOW + timedelta(seconds=60)).timestamp())

    monkeypatch.setattr(voice, "mint_client_secret", fake_mint)
    return calls


def start(client: TestClient, ip: str = "203.0.113.7"):
    return client.post(START, headers={"X-Forwarded-For": ip})


def sessions(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(VoiceSession))


def test_test_mode_needs_no_provider_and_returns_no_secret(client, db) -> None:
    use_settings(voice_mode="test")
    response = start(client)
    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "test"
    assert body["client_secret"] is None
    assert body["max_duration_seconds"] == 300
    assert sessions(db) == 1


def test_live_mode_returns_only_a_short_lived_secret(client, db, minted) -> None:
    use_settings(voice_mode="live", openai_api_key=FAKE_KEY)
    response = start(client)
    assert response.status_code == 201
    body = response.json()
    assert body["client_secret"] == "ek_test_secret"
    assert FAKE_KEY not in response.text
    assert minted[0]["key"] == FAKE_KEY
    record = db.scalar(select(VoiceSession))
    assert record.mode == "live"
    assert record.model == "gpt-realtime-2.1-mini"


def test_the_off_switch_disables_voice(client, db) -> None:
    use_settings(voice_mode="off")
    response = start(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_disabled"
    assert sessions(db) == 0


def test_live_mode_without_a_key_is_disabled_not_broken(client, db) -> None:
    use_settings(voice_mode="live", openai_api_key="")
    assert start(client).json()["error"]["code"] == "voice_disabled"


def test_daily_quota_caps_sessions(client, db) -> None:
    use_settings(voice_mode="test", voice_daily_session_limit=2)
    assert start(client, "198.51.100.1").status_code == 201
    assert start(client, "198.51.100.2").status_code == 201
    response = start(client, "198.51.100.3")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "voice_quota_reached"


def test_quota_resets_at_local_midnight(client, db) -> None:
    use_settings(voice_mode="test", voice_daily_session_limit=1)
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 5, 23, 50, tzinfo=LA)
    assert start(client).status_code == 201
    assert start(client, "198.51.100.9").status_code == 429
    app.dependency_overrides[get_now] = lambda: datetime(2027, 1, 6, 0, 5, tzinfo=LA)
    assert start(client, "198.51.100.9").status_code == 201


def test_one_visitor_is_rate_limited(client, db) -> None:
    use_settings(voice_mode="test", voice_sessions_per_client_per_hour=2)
    assert start(client).status_code == 201
    assert start(client).status_code == 201
    limited = start(client)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "too_many_requests"
    assert start(client, "203.0.113.99").status_code == 201
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=1, minutes=1)
    assert start(client).status_code == 201


def test_visitor_addresses_are_stored_only_as_hashes(client, db) -> None:
    use_settings(voice_mode="test")
    start(client, "203.0.113.7")
    record = db.scalar(select(VoiceSession))
    assert "203.0.113.7" not in record.client_hash


def test_provider_failure_is_reported_without_using_quota(client, db, monkeypatch) -> None:
    use_settings(voice_mode="live", openai_api_key=FAKE_KEY)

    def boom(*args, **kwargs):
        raise voice.urllib.error.URLError("unreachable")

    monkeypatch.setattr(voice.urllib.request, "urlopen", boom)
    response = start(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "voice_unavailable"
    assert FAKE_KEY not in response.text
    assert sessions(db) == 0


def test_secret_request_matches_the_documented_shape(monkeypatch) -> None:
    sent = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return json.dumps({"value": "ek_1", "expires_at": 1800000000}).encode()

    def fake_urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["headers"] = dict(request.header_items())
        sent["body"] = json.loads(request.data)
        sent["timeout"] = timeout
        return Response()

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    settings = Settings(voice_mode="live", openai_api_key=FAKE_KEY)
    assert voice.mint_client_secret(settings, "Be brief.", "abc") == ("ek_1", 1800000000)
    assert sent["url"] == "https://api.openai.com/v1/realtime/client_secrets"
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    body = sent["body"]
    assert body["expires_after"] == {"anchor": "created_at", "seconds": 60}
    assert body["session"]["type"] == "realtime"
    assert body["session"]["instructions"] == "Be brief."
    assert body["session"]["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert body["session"]["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"


def test_instructions_contain_only_database_facts_and_the_rules(db) -> None:
    business = db.scalar(select(Business).where(Business.slug == "pawcare-demo"))
    text = build_instructions(db, business)
    assert "PawCare Grooming Studio" in text
    assert "Full Groom" in text and "$90" in text
    assert "Monday: closed" in text and "Tuesday: 9 AM to 5 PM" in text
    assert "Which dogs do you groom?" in text
    assert "Never give veterinary or medical advice" in text
    assert "Never say an appointment is booked or confirmed" in text
    assert "@" not in text.replace("hello@pawcare.example", "")


def test_ending_records_the_outcome_once(client, db) -> None:
    use_settings(voice_mode="test")
    body = start(client).json()
    url = f"{START}/{body['session_id']}/end"
    payload = {
        "session_token": body["session_token"],
        "reason": "user_ended",
        "usage": {"input_tokens": 120, "output_tokens": 340, "responses": 3},
    }
    assert client.post(url, json=payload).status_code == 204
    assert client.post(url, json={**payload, "reason": "error"}).status_code == 204
    record = db.scalar(select(VoiceSession))
    db.refresh(record)
    assert record.status == "ended"
    assert record.end_reason == "user_ended"
    assert record.usage == {"input_tokens": 120, "output_tokens": 340, "responses": 3}


def test_ending_needs_the_session_token(client, db) -> None:
    use_settings(voice_mode="test")
    body = start(client).json()
    response = client.post(
        f"{START}/{body['session_id']}/end",
        json={"session_token": "x" * 43, "reason": "user_ended"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_session_not_found"


def test_unknown_end_reasons_are_rejected(client, db) -> None:
    use_settings(voice_mode="test")
    body = start(client).json()
    response = client.post(
        f"{START}/{body['session_id']}/end",
        json={"session_token": body["session_token"], "reason": "booked"},
    )
    assert response.status_code == 422
