"""Connected calendars are checked every 15 minutes. The owner and the admin get one email
when a calendar stops working and one when it works again."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import google_calendar
from app.google_calendar import CHECK_EVERY, CalendarAccessProblem, check_round
from app.integrations import CALENDAR, share_calendar
from app.models import Business, BusinessIntegration, WebhookEvent
from app.owner_email import send_due
from tests.test_calendar_connect import (  # noqa: F401 - fixtures
    CALENDAR_ID,
    EMAIL,
    SETTINGS,
    configured,
)
from tests.test_google_calendar import NOW
from tests.test_owner_email import OWNER, Outbox, settings

ADMIN = "admin@helloever.example"
NOT_SHARED = CalendarAccessProblem(
    "calendar_not_shared", f"We can't see that calendar yet. {EMAIL}"
)
UNREACHABLE = CalendarAccessProblem("calendar_unreachable", "Google Calendar couldn't be reached.")


@pytest.fixture
def google(monkeypatch) -> dict:
    """What the live check against Google answers: None for working, or a problem."""
    state: dict = {"problem": None, "checked": []}

    def fake_check(account_json: str, calendar_id: str) -> None:
        state["checked"].append(calendar_id)
        if state["problem"]:
            raise state["problem"]

    monkeypatch.setattr(google_calendar, "check_account_access", fake_check)
    return state


@pytest.fixture
def connected(db: Session) -> Iterator[BusinessIntegration]:
    db.execute(update(Business).where(Business.slug == "pawcare-demo").values(notify_email=OWNER))
    share_calendar(db, SETTINGS, "pawcare-demo", CALENDAR_ID)
    db.commit()
    yield row(db)
    db.rollback()
    db.execute(update(Business).where(Business.slug == "pawcare-demo").values(notify_email=None))
    db.commit()


def row(db: Session) -> BusinessIntegration:
    db.expire_all()
    return db.scalar(select(BusinessIntegration).where(BusinessIntegration.kind == CALENDAR))


def events(db: Session) -> list[tuple[str, dict]]:
    found = db.scalars(select(WebhookEvent).order_by(WebhookEvent.created_at)).all()
    return [(e.event_type, e.payload["data"]) for e in found]


def test_a_working_calendar_is_checked_every_15_minutes_quietly(db, google, connected) -> None:
    connected_at = connected.updated_at
    assert check_round(db, SETTINGS, NOW) == 1
    assert row(db).checked_at == NOW and row(db).problem_code is None
    assert check_round(db, SETTINGS, NOW + timedelta(minutes=10)) == 0
    assert check_round(db, SETTINGS, NOW + CHECK_EVERY) == 1
    assert events(db) == []
    # Checking doesn't change when it was connected.
    assert row(db).updated_at == connected_at


def test_an_unshared_calendar_alerts_once_then_says_when_it_works(db, google, connected) -> None:
    google["problem"] = NOT_SHARED
    check_round(db, SETTINGS, NOW)
    found = row(db)
    assert (found.problem_code, found.problem_since, found.alerted_at) == (
        "calendar_not_shared",
        NOW,
        NOW,
    )
    assert [kind for kind, _ in events(db)] == ["calendar.failing", "calendar.alert"]
    failing = events(db)[0][1]
    assert (
        failing["calendar_id"] == CALENDAR_ID and failing["problem_code"] == "calendar_not_shared"
    )

    # Still not shared: no second email.
    check_round(db, SETTINGS, NOW + CHECK_EVERY)
    assert len(events(db)) == 2
    assert row(db).problem_since == NOW

    google["problem"] = None
    check_round(db, SETTINGS, NOW + 2 * CHECK_EVERY)
    kinds = [kind for kind, _ in events(db)]
    assert kinds[2:] == ["calendar.working", "calendar.alert"]
    assert events(db)[3][1]["working"] is True
    found = row(db)
    assert (found.problem_code, found.problem_since, found.alerted_at) == (None, None, None)


def test_google_briefly_unreachable_is_not_worth_an_email(db, google, connected) -> None:
    google["problem"] = UNREACHABLE
    check_round(db, SETTINGS, NOW)
    check_round(db, SETTINGS, NOW + CHECK_EVERY)
    assert events(db) == []
    check_round(db, SETTINGS, NOW + 2 * CHECK_EVERY)
    assert [kind for kind, _ in events(db)] == ["calendar.failing", "calendar.alert"]


def test_a_short_outage_that_recovers_sends_nothing(db, google, connected) -> None:
    google["problem"] = UNREACHABLE
    check_round(db, SETTINGS, NOW)
    google["problem"] = None
    check_round(db, SETTINGS, NOW + CHECK_EVERY)
    assert events(db) == []
    assert row(db).problem_since is None


def test_reconnecting_checks_afresh(db, google, connected) -> None:
    google["problem"] = NOT_SHARED
    check_round(db, SETTINGS, NOW)
    share_calendar(db, SETTINGS, "pawcare-demo", "team@gmail.com")
    db.commit()
    found = row(db)
    assert (found.checked_at, found.problem_code, found.alerted_at) == (None, None, None)
    assert check_round(db, SETTINGS, NOW + timedelta(minutes=1)) == 1


def test_settings_that_cannot_be_read_alert_at_once(db, google, connected) -> None:
    check_round(db, SETTINGS.model_copy(update={"google_service_account_json": ""}), NOW)
    assert events(db)[0][1]["problem_code"] == "calendar_settings_unreadable"
    assert google["checked"] == []


def test_the_owner_and_the_admin_are_emailed(db, google, connected) -> None:
    google["problem"] = NOT_SHARED
    check_round(db, SETTINGS, NOW)
    outbox = Outbox()
    assert send_due(db, settings(admin_email=ADMIN), NOW, outbox) == 2
    owner, admin = sorted(outbox.sent, key=lambda m: m["To"] != OWNER)
    assert owner["To"] == OWNER
    assert "can't use your Google Calendar" in owner["Subject"]
    body = owner.get_content()
    assert "We can't see that calendar yet" in body
    assert "callers and your booking site can't book" in body
    assert "/b/pawcare-demo/staff" in body
    assert admin["To"] == ADMIN
    assert "Calendar failing" in admin["Subject"]
    assert "pawcare-demo" in admin.get_content()

    google["problem"] = None
    check_round(db, SETTINGS, NOW + CHECK_EVERY)
    outbox = Outbox()
    send_due(db, settings(admin_email=ADMIN), NOW + CHECK_EVERY, outbox)
    subjects = sorted(m["Subject"] for m in outbox.sent)
    assert any("works again" in s for s in subjects)
    assert any("Calendar working again" in s for s in subjects)
