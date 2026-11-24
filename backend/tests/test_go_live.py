"""The owner's chosen voice, and the emails telling them they're live."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import go_live
from app.config import Settings, get_settings
from app.models import Business, BusinessPhoneNumber, WebhookEvent
from app.owner_email import message_for, notice_address
from app.phone import accept_body
from app.phone_numbers import add_number
from app.provision import approve
from app.vapi import vapi_voice
from tests.test_subscribers import API, auth, complete, fixed_clock  # noqa: F401

SITE = "https://book.helloever.example"


@pytest.fixture(autouse=True)
def clean(db: Session) -> Iterator[None]:
    def remove() -> None:
        db.rollback()
        db.execute(delete(Business).where(Business.slug.like("shaggy-chic%")))
        db.commit()

    remove()
    get_settings.cache_clear()
    yield
    remove()
    get_settings.cache_clear()


@pytest.fixture
def live(client: TestClient, db: Session, monkeypatch) -> Business:
    """Sam's profile (male voice, no calendar connected), approved."""
    monkeypatch.setenv("PUBLIC_SITE_URL", SITE + "/")
    monkeypatch.setenv("ADMIN_EMAIL", "team@helloever.example")
    get_settings.cache_clear()
    headers = auth(client)
    complete(client, headers)
    assert client.post(f"{API}/profile/submit", headers=headers).status_code == 200
    seed, _, _ = approve(db, "sam@example.com")
    db.commit()
    return db.scalar(select(Business).where(Business.slug == seed.slug))


def notices(db: Session, event_type: str) -> list[WebhookEvent]:
    return db.scalars(select(WebhookEvent).where(WebhookEvent.event_type == event_type)).all()


# The voice


def test_approve_keeps_the_voice_the_owner_chose(live) -> None:
    assert live.voice == "cedar"


def test_calls_use_the_business_voice_over_the_deployment_one() -> None:
    settings = Settings(voice_name="marin", phone_voice="marin")
    assert accept_body(settings, "Hi", [], "cedar")["audio"]["output"]["voice"] == "cedar"
    assert accept_body(settings, "Hi", [], None)["audio"]["output"]["voice"] == "marin"


def test_browser_sessions_use_the_business_voice(monkeypatch) -> None:
    from app import voice

    sent = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"value": "ek_1", "expires_at": 1800000000}'

    def urlopen(request, timeout):
        import json

        sent.update(json.loads(request.data))
        return Reply()

    monkeypatch.setattr(voice.urllib.request, "urlopen", urlopen)
    voice.mint_client_secret(Settings(openai_api_key="k"), "Hi", "abc", voice="cedar")
    assert sent["session"]["audio"]["output"]["voice"] == "cedar"


def test_vapi_calls_get_the_male_voice_only_from_elevenlabs() -> None:
    male, female = Business(voice="cedar"), Business(voice="marin")
    settings = Settings(vapi_voice_id="rachel", vapi_male_voice_id="adam")
    assert vapi_voice(settings, male) == "adam"
    assert vapi_voice(settings, female) == "rachel"
    assert vapi_voice(settings, Business(voice=None)) == "rachel"
    other = Settings(vapi_voice_provider="openai", vapi_voice_id="alloy", vapi_male_voice_id="adam")
    assert vapi_voice(other, male) == "alloy"


# You're live


def test_approve_emails_the_owner_their_booking_page_and_dashboard(db, live) -> None:
    [event] = notices(db, go_live.LIVE)
    settings = get_settings()
    assert notice_address(event, settings) == "sam@example.com"
    message = message_for(event, None, settings, "sam@example.com")
    body = message.get_content()
    assert message["Subject"] == "Shaggy Chic is set up with HelloEver AI"
    assert message["Reply-To"] == "team@helloever.example"
    assert "Hi Sam," in body
    assert f"Your booking page: {SITE}/b/shaggy-chic\n" in body
    assert f"Your staff dashboard: {SITE}/b/shaggy-chic/staff\n" in body
    assert "email and password you signed up with" in body
    assert "Your calendar: not connected." in body
    assert "we'll email you as soon as your receptionist answers calls" in body
    # It promises no number yet.
    assert "+1" not in body


def test_a_connected_calendar_is_named() -> None:
    _, lines = go_live.compose(
        go_live.LIVE,
        {"owner_name": "Sam", "business_name": "Shaggy Chic", "calendar_id": "sam@gmail.com"},
        "HelloEver AI",
    )
    assert any("Google Calendar (sam@gmail.com)" in line for line in lines)


# The phone number


def test_the_first_number_emails_the_owner_how_to_use_it(db, live) -> None:
    add_number(db, live.slug, "+14085550199")
    add_number(db, live.slug, "+14085550199")
    add_number(db, live.slug, "+14085550188")
    db.commit()
    [event] = notices(db, go_live.PHONE_READY)
    settings = get_settings()
    assert notice_address(event, settings) == "sam@example.com"
    message = message_for(event, None, settings, "sam@example.com")
    body = message.get_content()
    assert message["Subject"] == "Your receptionist answers at (408) 555-0199"
    assert "Calls to (408) 555-0199 are now answered by your receptionist for Shaggy Chic" in body
    assert "it's an AI assistant" in body
    assert f"dashboard ({SITE}/b/shaggy-chic/staff)" in body
    assert "ask your phone provider to forward calls to (408) 555-0199" in body


def test_numbers_for_businesses_without_a_sign_up_email_nobody(db, second_business) -> None:
    add_number(db, second_business, "+13105550142")
    db.commit()
    assert notices(db, go_live.PHONE_READY) == []
    db.execute(delete(BusinessPhoneNumber))
    db.commit()


def test_the_demo_never_gets_one(db) -> None:
    demo = db.scalar(select(Business).where(Business.is_demo))
    assert go_live.tell_phone_ready(db, get_settings(), demo, "+13105550142") is False


@pytest.mark.parametrize(
    ("number", "spoken"), [("+14085550199", "(408) 555-0199"), ("+442071838750", "+442071838750")]
)
def test_numbers_are_written_the_way_people_read_them(number, spoken) -> None:
    assert go_live.spoken_number(number) == spoken


def test_approve_starts_a_free_trial_and_says_so(db, live) -> None:
    from datetime import UTC, datetime, timedelta

    assert live.plan == "trial"
    left = live.trial_ends_at - datetime.now(UTC)
    assert timedelta(days=13, hours=23) < left <= timedelta(days=14)
    [event] = notices(db, go_live.LIVE)
    body = message_for(event, None, get_settings(), "sam@example.com").get_content()
    assert "Your free trial: 14 days, until" in body
    assert "choose a monthly plan on your dashboard (Starter $49 for 100 calls; Growth $99 " in body
    assert "Pro $149 for 500 calls). No card is needed for the trial" in body
