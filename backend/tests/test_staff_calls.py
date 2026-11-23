"""The staff dashboard's list of calls: phone and website conversations and what came of each."""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app, get_now
from app.models import Business, CallbackRequest, VoiceSession
from tests.factories import make_booking
from tests.test_voice_tools import staff_headers  # noqa: F401 - a fixture

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 12, 0, tzinfo=LA)


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def demo_id(db: Session):
    return db.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))


def a_call(db: Session, minutes_ago: int, **fields) -> VoiceSession:
    started = NOW - timedelta(minutes=minutes_ago)
    defaults = {
        "id": uuid.uuid4(),
        "business_id": demo_id(db),
        "mode": "live",
        "channel": "phone",
        "client_hash": "0" * 64,
        "token_hash": uuid.uuid4().hex * 2,
        "started_at": started,
        "status": "ended",
        "ended_at": started + timedelta(minutes=3),
        "end_reason": "caller_hung_up",
    }
    call = VoiceSession(**{**defaults, **fields})
    db.add(call)
    db.flush()
    return call


def calls(client, headers) -> list[dict]:
    response = client.get("/api/v1/staff/calls", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_calls_are_listed_newest_first_with_what_came_of_them(
    client,
    db,
    staff_headers,  # noqa: F811 - the imported fixture
) -> None:
    booked = a_call(db, 300, booking_reference="PC-BOOKED23")
    website = a_call(
        db, 200, channel="web", end_reason="page_closed", ended_at=NOW - timedelta(minutes=199)
    )
    db.add(
        CallbackRequest(
            business_id=demo_id(db),
            voice_session_id=website.id,
            name="Sam",
            phone="(323) 555-0100",
            reason="health",
        )
    )
    moved = make_booking(db, NOW + timedelta(days=1), key="moved")
    moved_call = a_call(db, 100, managed_booking_id=moved.id, tool_failures=2)
    moved.changed_at = NOW - timedelta(minutes=99)
    gone = make_booking(db, NOW + timedelta(days=2), key="gone")
    gone_call = a_call(
        db, 50, managed_booking_id=gone.id, usage={"cost_usd": 0.4213, "duration_seconds": 61.2}
    )
    gone.status, gone.cancelled_via = "cancelled", "caller"
    gone.cancelled_at = NOW - timedelta(minutes=49)
    limited = a_call(db, 20, message_only=True, end_reason="vapi:something-new")
    live = a_call(db, 2, status="started", ended_at=None, end_reason=None)
    db.commit()

    rows = calls(client, staff_headers)
    assert [row["id"] for row in rows] == [
        str(c.id) for c in (live, limited, gone_call, moved_call, website, booked)
    ]
    by_id = {row["id"]: row for row in rows}

    first = by_id[str(booked.id)]
    assert first["channel"] == "phone" and first["booked_reference"] == "PC-BOOKED23"
    assert first["duration_seconds"] == 180 and first["ended_by"] == "Caller hung up"
    assert first["local_start"].startswith("2027-01-04T07:00:00-08:00")

    web = by_id[str(website.id)]
    assert web["channel"] == "website" and web["callback_requested"] is True
    assert web["ended_by"] == "Visitor left the page" and web["duration_seconds"] == 60

    assert by_id[str(moved_call.id)]["changed_reference"] == moved.reference
    assert by_id[str(moved_call.id)]["cancelled_reference"] is None
    assert by_id[str(moved_call.id)]["tool_failures"] == 2
    cancelled = by_id[str(gone_call.id)]
    assert cancelled["cancelled_reference"] == gone.reference
    assert cancelled["changed_reference"] is None and cancelled["cost_usd"] == 0.4213

    assert by_id[str(limited.id)]["message_only"] is True
    assert by_id[str(limited.id)]["ended_by"] == "Ended"
    assert by_id[str(live.id)]["in_progress"] is True
    assert by_id[str(live.id)]["duration_seconds"] is None


def test_a_change_outside_the_call_is_not_credited_to_it(
    client,
    db,
    staff_headers,  # noqa: F811 - the imported fixture
) -> None:
    booking = make_booking(db, NOW + timedelta(days=1), key="later")
    call = a_call(db, 300, managed_booking_id=booking.id)
    booking.changed_at = NOW - timedelta(minutes=10)
    stale = a_call(db, 180, status="started", ended_at=None, end_reason=None)
    db.commit()
    rows = {row["id"]: row for row in calls(client, staff_headers)}
    assert rows[str(call.id)]["changed_reference"] is None
    # Never reported its end: not shown as still going on hours later.
    assert rows[str(stale.id)]["in_progress"] is False


def test_staff_see_only_their_own_business_and_must_sign_in(
    client,
    db,
    second_business,
    staff_headers,  # noqa: F811 - the imported fixture
) -> None:
    other = db.scalar(select(Business.id).where(Business.slug == second_business))
    a_call(db, 30, business_id=other)
    mine = a_call(db, 20)
    db.commit()
    assert [row["id"] for row in calls(client, staff_headers)] == [str(mine.id)]
    assert client.get("/api/v1/staff/calls").status_code == 401
    assert client.get("/api/v1/staff/calls?limit=0", headers=staff_headers).status_code == 422
