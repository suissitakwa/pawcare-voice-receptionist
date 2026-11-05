"""Exact rule tests for the pure slot calculation, with no database involved."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.availability import DayRules, Interval, compute_slots

LA = ZoneInfo("America/Los_Angeles")
TUESDAY = date(2027, 1, 5)
LONG_BEFORE = datetime(2026, 12, 1, tzinfo=UTC)

RULES = DayRules(
    timezone=LA,
    business_hours=(time(9), time(17)),
    groomer_hours=(time(9), time(17)),
    breaks=(),
    blocked=(),
    booked=(),
    increment=timedelta(minutes=15),
    buffer=timedelta(minutes=15),
    min_notice=timedelta(0),
)


def la(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA)


def starts(rules: DayRules = RULES, minutes: int = 30, day: date = TUESDAY, now=LONG_BEFORE):
    slots = compute_slots(day, timedelta(minutes=minutes), rules, now)
    return [slot.starts_at.astimezone(rules.timezone).strftime("%H:%M") for slot in slots]


def test_first_slot_is_opening_and_last_ends_exactly_at_closing() -> None:
    result = starts()
    assert result[0] == "09:00"
    assert result[-1] == "16:30"


def test_appointment_that_would_overrun_closing_is_not_offered() -> None:
    assert starts(minutes=150)[-1] == "14:30"
    assert starts(minutes=8 * 60) == ["09:00"]
    assert starts(minutes=8 * 60 + 1) == []


def test_starts_follow_the_configured_increment() -> None:
    assert starts(replace(RULES, increment=timedelta(minutes=30)))[:3] == [
        "09:00",
        "09:30",
        "10:00",
    ]


def test_slot_end_reflects_duration_without_buffer() -> None:
    slot = compute_slots(TUESDAY, timedelta(minutes=90), RULES, LONG_BEFORE)[0]
    assert slot.ends_at - slot.starts_at == timedelta(minutes=90)


def test_closed_business_or_absent_groomer_has_no_slots() -> None:
    assert starts(replace(RULES, business_hours=None)) == []
    assert starts(replace(RULES, groomer_hours=None)) == []


def test_only_the_overlap_of_business_and_groomer_hours_is_bookable() -> None:
    result = starts(replace(RULES, groomer_hours=(time(10), time(15))))
    assert result[0] == "10:00"
    assert result[-1] == "14:30"


def test_hours_that_do_not_overlap_have_no_slots() -> None:
    rules = replace(RULES, business_hours=(time(9), time(12)), groomer_hours=(time(12), time(17)))
    assert starts(rules) == []


def test_appointment_may_end_when_a_break_starts_and_start_when_it_ends() -> None:
    result = starts(replace(RULES, breaks=((time(12), time(12, 30)),)))
    assert "11:30" in result
    assert "11:45" not in result
    assert "12:00" not in result
    assert "12:15" not in result
    assert "12:30" in result


def test_long_appointment_cannot_span_a_break() -> None:
    result = starts(replace(RULES, breaks=((time(12), time(12, 30)),)), minutes=150)
    assert "09:30" in result
    assert "09:45" not in result
    assert "12:15" not in result


def test_blocked_period_removes_every_overlapping_start() -> None:
    blocked = (Interval(la(TUESDAY, 10), la(TUESDAY, 11)),)
    result = starts(replace(RULES, blocked=blocked))
    assert "09:30" in result
    assert "09:45" not in result
    assert "10:45" not in result
    assert "11:00" in result


def test_block_spanning_several_days_closes_the_whole_day() -> None:
    blocked = (Interval(la(date(2027, 1, 4), 12), la(date(2027, 1, 6), 12)),)
    assert starts(replace(RULES, blocked=blocked)) == []


def test_buffer_applies_before_an_existing_booking() -> None:
    booked = (Interval(la(TUESDAY, 10), la(TUESDAY, 10, 45)),)
    result = starts(replace(RULES, booked=booked))
    assert "09:15" in result  # Ends 09:45, and its buffer clears at 10:00.
    assert "09:30" not in result  # Ends 10:00, but its buffer runs into the booking.


def test_buffer_applies_after_an_existing_booking() -> None:
    booked = (Interval(la(TUESDAY, 10), la(TUESDAY, 10, 45)),)
    result = starts(replace(RULES, booked=booked))
    assert "10:30" not in result
    assert "10:45" in result


def test_zero_buffer_allows_back_to_back_appointments() -> None:
    booked = (Interval(la(TUESDAY, 10), la(TUESDAY, 10, 30)),)
    result = starts(replace(RULES, booked=booked, buffer=timedelta(0)))
    assert "09:30" in result
    assert "10:30" in result


def test_trailing_buffer_may_run_past_closing_and_into_a_break() -> None:
    result = starts(replace(RULES, breaks=((time(12), time(12, 30)),)))
    assert "11:30" in result
    assert result[-1] == "16:30"


def test_minimum_notice_hides_starts_that_are_too_soon() -> None:
    now = la(TUESDAY, 9, 30)
    assert starts(replace(RULES, min_notice=timedelta(hours=2)), now=now)[0] == "11:30"


def test_start_exactly_at_the_notice_limit_is_allowed_but_one_minute_late_is_not() -> None:
    rules = replace(RULES, min_notice=timedelta(hours=1))
    assert starts(rules, now=la(TUESDAY, 9))[0] == "10:00"
    assert starts(rules, now=la(TUESDAY, 9, 1))[0] == "10:15"


def test_nothing_is_offered_once_the_day_has_passed_the_notice_limit() -> None:
    assert starts(now=la(TUESDAY, 18)) == []


def test_results_are_utc_and_track_the_seasonal_offset() -> None:
    winter = compute_slots(TUESDAY, timedelta(minutes=30), RULES, LONG_BEFORE)[0]
    summer_day = date(2027, 7, 6)
    summer = compute_slots(summer_day, timedelta(minutes=30), RULES, LONG_BEFORE)[0]
    assert winter.starts_at == datetime(2027, 1, 5, 17, 0, tzinfo=UTC)
    assert summer.starts_at == datetime(2027, 7, 6, 16, 0, tzinfo=UTC)


def test_spring_forward_day_has_one_hour_less_to_book() -> None:
    # Clocks jump from 02:00 to 03:00 on Sunday 14 March 2027.
    rules = replace(RULES, business_hours=(time(1), time(4)), groomer_hours=(time(1), time(4)))
    day = date(2027, 3, 14)
    assert compute_slots(day, timedelta(hours=2), rules, LONG_BEFORE)[0].ends_at == datetime(
        2027, 3, 14, 11, 0, tzinfo=UTC
    )
    assert compute_slots(day, timedelta(minutes=121), rules, LONG_BEFORE) == []
    result = starts(rules, day=day)
    assert "02:00" not in result
    assert "02:45" not in result
    assert result[:5] == ["01:00", "01:15", "01:30", "01:45", "03:00"]


def test_fall_back_day_has_one_hour_more_to_book() -> None:
    # Clocks return from 02:00 to 01:00 on Sunday 7 November 2027.
    rules = replace(RULES, business_hours=(time(0), time(3)), groomer_hours=(time(0), time(3)))
    day = date(2027, 11, 7)
    assert compute_slots(day, timedelta(hours=4), rules, LONG_BEFORE)
    assert compute_slots(day, timedelta(minutes=241), rules, LONG_BEFORE) == []
    slots = compute_slots(day, timedelta(minutes=15), rules, LONG_BEFORE)
    instants = [slot.starts_at for slot in slots]
    assert len(instants) == len(set(instants))
    assert all(b - a == timedelta(minutes=15) for a, b in zip(instants, instants[1:], strict=False))


def test_normal_hours_stay_at_the_same_wall_clock_across_a_clock_change() -> None:
    before = starts(day=date(2027, 3, 13))
    after = starts(day=date(2027, 3, 14))
    assert before == after
    assert after[0] == "09:00"
