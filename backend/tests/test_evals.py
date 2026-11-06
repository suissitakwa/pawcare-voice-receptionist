"""The evaluation harness and checks, run offline with a scripted model. No provider calls."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(EVALS))

from pawcare_evals.checks import check, evaluate, times_in  # noqa: E402
from pawcare_evals.harness import run_scenario  # noqa: E402
from pawcare_evals.model import ModelReply  # noqa: E402

SPEC = json.loads((EVALS / "scenarios.json").read_text())
TODAY = datetime.fromisoformat(SPEC["today"])
SCENARIOS = {s["id"]: s for s in SPEC["scenarios"]}
NINE = "2027-01-05T17:00:00+00:00"


class ScriptedModel:
    """Plays back replies. A callable reply sees the conversation so far."""

    model = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages: list[tuple[str, str]] = []
        self.outputs: list[dict] = []

    async def connect(self, instructions, tools):
        self.instructions = instructions

    async def add_message(self, role, text):
        self.messages.append((role, text))

    async def add_tool_output(self, call_id, output):
        self.outputs.append(output)

    async def respond(self):
        reply = self.replies.pop(0) if self.replies else ModelReply(text="")
        return reply(self) if callable(reply) else reply

    async def close(self):
        pass


def say(text):
    return ModelReply(
        text=text, usage={"input_tokens": 10, "output_tokens": 5}, first_output_ms=100
    )


def tool(name, **args):
    return ModelReply(
        text="", calls=[{"name": name, "call_id": f"c-{name}", "arguments": json.dumps(args)}]
    )


def read_reference(model):
    reference = model.messages[-1][1].split("Reference ")[1].split(".")[0]
    return say(f"You're booked. Your reference is {' '.join(reference)}.")


HAPPY = [
    tool("check_availability", service="full-groom", size="medium", date="2027-01-05"),
    say("A full groom for a medium dog takes 120 minutes, $90. I have 9 AM, 9:15 AM, or 9:30 AM."),
    say("9 AM it is. What are your name, your dog's name, and a phone number?"),
    say("Thanks. Emily, E M I L Y, dog Nala, phone 6 6 9, 3 3 3, 1 2 4 1. Is that right?"),
    tool(
        "prepare_booking",
        service="full-groom",
        size="medium",
        starts_at=NINE,
        owner_name="Emily",
        pet_name="Nala",
        owner_phone="669 333 1241",
        details_read_back=True,
    ),
    say("The summary is on your screen. Please press Confirm booking to book it."),
    read_reference,
    say("You're welcome!"),
]


def run(db: Session, scenario_id: str, replies) -> tuple:
    scenario = SCENARIOS[scenario_id]
    record = asyncio.run(run_scenario(db, ScriptedModel(replies), scenario, TODAY))
    return record, {r["type"]: r for r in evaluate(record, scenario, db)}


def test_scenario_file_covers_the_required_ground() -> None:
    assert len(SPEC["scenarios"]) >= 30
    assert len(SCENARIOS) == len(SPEC["scenarios"])
    categories = {s["category"] for s in SPEC["scenarios"]}
    for required in (
        "faq",
        "booking",
        "interruption",
        "change",
        "correction",
        "unsupported",
        "conflict",
        "failure",
        "callback",
    ):
        assert required in categories


def test_every_check_type_in_the_scenarios_is_implemented(db: Session) -> None:
    from pawcare_evals.harness import Record

    empty = Record()
    for scenario in SPEC["scenarios"]:
        for spec in scenario["checks"]:
            check(empty, spec, db)


def test_a_correct_booking_passes_every_check(db: Session) -> None:
    record, results = run(db, "book-happy-path", HAPPY)
    failed = {name: r["detail"] for name, r in results.items() if not r["passed"]}
    assert failed == {}
    assert record.references and record.references[0].startswith("PC-")


def test_claiming_booked_before_the_click_is_caught(db: Session) -> None:
    replies = list(HAPPY)
    replies[5] = say("Great news, you're booked for 9 AM!")
    _, results = run(db, "book-happy-path", replies)
    assert results["no_premature_confirmation"]["passed"] is False
    assert "you're booked" in results["no_premature_confirmation"]["detail"].lower()


def test_saying_not_booked_yet_is_not_a_false_alarm(db: Session) -> None:
    replies = list(HAPPY)
    replies[5] = say("It is not booked yet. Press Confirm booking on your screen.")
    _, results = run(db, "book-happy-path", replies)
    assert results["no_premature_confirmation"]["passed"] is True


def test_offering_a_time_the_tool_did_not_return_is_caught(db: Session) -> None:
    replies = list(HAPPY)
    replies[1] = say("I have 8 AM, 9 AM, or 11 AM.")
    _, results = run(db, "book-happy-path", replies)
    assert results["offered_times_valid"]["passed"] is False
    assert "8 AM" in results["offered_times_valid"]["detail"]


def test_skipping_the_read_back_is_caught(db: Session) -> None:
    replies = list(HAPPY)
    replies[3] = say("Thanks!")
    _, results = run(db, "book-happy-path", replies)
    assert results["read_back_before_prepare"]["passed"] is False


def test_slot_taken_before_the_click_books_nothing_for_the_caller(db: Session) -> None:
    replies = [
        tool("check_availability", service="full-groom", size="medium", date="2027-01-05"),
        say("I have 9 AM."),
        say("Emily, E M I L Y, Nala, 6 6 9 3 3 3 1 2 4 1. Correct?"),
        tool(
            "prepare_booking",
            service="full-groom",
            size="medium",
            starts_at=NINE,
            owner_name="Emily",
            pet_name="Nala",
            owner_phone="669 333 1241",
            details_read_back=True,
        ),
        say("Please press Confirm booking."),
        say("Sorry, that time was just taken. Shall I check other times?"),
        tool("check_availability", service="full-groom", size="medium", date="2027-01-05"),
        say("I have 12:30 PM or 1 PM."),
    ]
    record, results = run(db, "slot-taken-before-confirm", replies)
    assert record.conflict_at is not None
    assert results["bookings"]["passed"] is True
    assert results["tool_called_after_conflict"]["passed"] is True
    assert results["offered_times_valid"]["passed"] is True


def test_a_backend_failure_reaches_the_model_as_unavailable(db: Session) -> None:
    replies = [
        tool("check_availability", service="full-groom", size="medium", date="2027-01-05"),
        say("Sorry, I can't reach the booking system. Would you like a callback?"),
    ]
    model = ScriptedModel(replies)
    record = asyncio.run(run_scenario(db, model, SCENARIOS["backend-down-availability"], TODAY))
    assert model.outputs[0]["error"] == "tool_unavailable"
    results = {r["type"]: r for r in evaluate(record, SCENARIOS["backend-down-availability"], db)}
    assert all(r["passed"] for r in results.values())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We have 9 AM, 9:30 a.m. and 12:30 PM.", ["9 AM", "9:30 AM", "12:30 PM"]),
        ("Open 9am to 5pm", ["9 AM", "5 PM"]),
        ("1:00 PM works", ["1 PM"]),
    ],
)
def test_times_are_normalised_for_comparison(text, expected) -> None:
    assert times_in(text) == expected


def test_report_is_labelled_and_built_only_from_results(db: Session) -> None:
    from pawcare_evals.report import render

    record, results = run(db, "book-happy-path", HAPPY)
    report = render(
        {
            "model": "scripted",
            "run_at": "20270104T160000Z",
            "today": SPEC["today"],
            "scenario_count": 1,
            "runs": [
                {
                    "id": "book-happy-path",
                    "category": "booking",
                    "checks": list(results.values()),
                    "tool_calls": 2,
                    "tool_failures": 0,
                    "first_output_ms": [100.0, 140.0],
                    "input_tokens": 70,
                    "output_tokens": 35,
                }
            ],
        }
    )
    assert "not a voice measurement" in report
    assert "| Scenarios with every check passing | 1 of 1 (100%) |" in report
    assert "| booking | 1 of 1 |" in report
