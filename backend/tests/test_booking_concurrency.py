"""Simultaneous confirmations against real PostgreSQL, each on its own connection.

A barrier releases every thread at once, so the application's early availability check
passes for all of them and only the database constraints can decide the outcome.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.availability import load_business
from app.booking import confirm_booking, prepare_booking
from app.db import get_engine
from app.errors import ApiError
from app.models import Booking, Business
from app.schemas import PrepareBookingRequest

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=LA)


def business_id() -> object:
    with Session(get_engine()) as session:
        return session.scalar(select(Business.id).where(Business.slug == "pawcare-demo"))


def prepared(start: datetime, pet: str) -> dict:
    with Session(get_engine()) as session:
        draft = prepare_booking(
            session,
            load_business(session, business_id()),
            PrepareBookingRequest(
                service="full-groom",
                size="medium",
                starts_at=start,
                owner_name=f"Owner of {pet}",
                owner_email=f"{pet.lower()}@example.com",
                pet_name=pet,
            ),
            NOW,
        )
        session.commit()
        return {"token": draft.confirmation_token, "hash": draft.summary_hash}


def race(attempts: list[tuple[dict, str]]) -> list[tuple[str, str | bool]]:
    """Confirms every attempt at the same moment. Returns (outcome, detail) per attempt."""
    barrier = threading.Barrier(len(attempts))

    def attempt(draft: dict, key: str):
        with Session(get_engine()) as session:
            business = load_business(session, business_id())
            barrier.wait()
            try:
                confirmation, created = confirm_booking(
                    session, business, draft["token"], draft["hash"], key, NOW
                )
                session.commit()
                return ("booked", confirmation.reference, created)
            except ApiError as error:
                session.rollback()
                return ("error", error.code, None)

    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        return list(pool.map(lambda pair: attempt(*pair), attempts))


def confirmed_count() -> int:
    with Session(get_engine()) as session:
        return session.scalar(
            select(func.count()).select_from(Booking).where(Booking.status == "confirmed")
        )


def overlapping_pairs() -> int:
    with Session(get_engine()) as session:
        return session.scalar(
            text(
                """
                SELECT count(*) FROM bookings a JOIN bookings b
                  ON a.id < b.id AND a.groomer_id = b.groomer_id
                 AND a.status = 'confirmed' AND b.status = 'confirmed'
                 AND tstzrange(a.starts_at, a.occupied_until)
                  && tstzrange(b.starts_at, b.occupied_until)
                """
            )
        )


@pytest.mark.parametrize("round_number", range(3))
def test_many_customers_racing_for_one_slot_get_exactly_one_booking(db, round_number) -> None:
    start = datetime(2027, 1, 5, 9, 0, tzinfo=LA)
    attempts = [(prepared(start, f"Dog{i}"), f"race-{round_number}-{i:02d}") for i in range(12)]
    results = race(attempts)
    booked = [r for r in results if r[0] == "booked"]
    rejected = [r for r in results if r[0] == "error"]
    assert len(booked) == 1
    assert {r[1] for r in rejected} == {"slot_unavailable"}
    assert len(rejected) == 11
    assert confirmed_count() == 1
    assert overlapping_pairs() == 0


def test_overlapping_starts_racing_never_double_book(db) -> None:
    starts = [datetime(2027, 1, 5, 9, m, tzinfo=LA) for m in (0, 15, 30, 45)] * 3
    attempts = [(prepared(s, f"Pup{i}"), f"overlap-{i:02d}") for i, s in enumerate(starts)]
    results = race(attempts)
    assert sum(1 for r in results if r[0] == "booked") == 1
    assert {r[1] for r in results if r[0] == "error"} == {"slot_unavailable"}
    assert overlapping_pairs() == 0


def test_non_overlapping_slots_can_all_be_booked_at_once(db) -> None:
    # 9:00 to 11:15 and 12:30 to 14:45 occupy separate parts of the day.
    starts = [datetime(2027, 1, 5, 9, 0, tzinfo=LA), datetime(2027, 1, 5, 12, 30, tzinfo=LA)]
    attempts = [(prepared(s, f"Solo{i}"), f"solo-{i:02d}") for i, s in enumerate(starts)]
    results = race(attempts)
    assert [r[0] for r in results] == ["booked", "booked"]
    assert confirmed_count() == 2


def test_the_same_request_retried_at_once_creates_one_booking(db) -> None:
    draft = prepared(datetime(2027, 1, 5, 9, 0, tzinfo=LA), "Retry")
    results = race([(draft, "same-key-0001")] * 10)
    assert all(r[0] == "booked" for r in results)
    assert len({r[1] for r in results}) == 1
    assert sum(1 for r in results if r[2] is True) == 1
    assert confirmed_count() == 1


def test_one_summary_confirmed_with_different_keys_at_once_books_once(db) -> None:
    draft = prepared(datetime(2027, 1, 5, 9, 0, tzinfo=LA), "Twice")
    results = race([(draft, f"different-key-{i:02d}") for i in range(8)])
    assert sum(1 for r in results if r[0] == "booked") == 1
    assert {r[1] for r in results if r[0] == "error"} == {"already_confirmed"}
    assert confirmed_count() == 1
