"""The simulated-call grading in evals/pawcare_evals/vapi_sim.py, offline. No Vapi calls."""

import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(EVALS))

from pawcare_evals import vapi_sim  # noqa: E402

SPEC = json.loads((EVALS / "voice_scenarios.json").read_text())
SCENARIOS = {s["id"]: s for s in SPEC["scenarios"]}
CALLER = "+12605550100"


def say(text, time=None, end=None):
    return {"role": "bot", "message": text, "time": time, "endTime": end}


def hear(text, time=None, end=None):
    return {"role": "user", "message": text, "time": time, "endTime": end}


def tool(call_id, name, args):
    return {
        "role": "tool_calls",
        "toolCalls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def result(call_id, name, output):
    return {
        "role": "tool_call_result",
        "toolCallId": call_id,
        "name": name,
        "result": json.dumps(output),
    }


PREPARE = {
    "service": "bath-and-tidy",
    "size": "small",
    "starts_at": "2026-11-24T17:00:00+00:00",
    "owner_name": "Jordan Simms",
    "pet_name": "Biscuit",
    "details_read_back": True,
}


def booking_call(read_back=True, early_claim=False):
    messages = [
        {"role": "system", "message": "instructions"},
        say("Hi, thanks for calling PawCare! I'm the AI assistant.", 1000, 3000),
        hear("I'd like to book a bath for my dog.", 3500, 5000),
        tool("a", "check_availability", {"service": "bath-and-tidy", "size": "small"}),
        result("a", "check_availability", {"ok": True, "times": []}),
        say("I have nine a.m. Tuesday.", 5800, 7000),
        hear("That works.", 7500, 8000),
        tool("b", "prepare_booking", PREPARE),
        result("b", "prepare_booking", {"ok": True, "summary": "..."}),
    ]
    if early_claim:
        messages.append(say("Great, you're booked!", 8600, 9000))
    if read_back:
        messages += [
            say("A bath and tidy for Biscuit, Tuesday at nine. Shall I book it?", 8700, 11000),
            hear("Yes please.", 11500, 12000),
        ]
    messages += [
        tool("c", "confirm_booking", {"caller_confirmed": True}),
        result("c", "confirm_booking", {"ok": True, "reference": "PC-TEST1234"}),
        say("You're booked. Your reference is PC-TEST1234.", 12900, 15000),
    ]
    return {
        "id": "call-1",
        "endedReason": "customer-ended-call",
        "artifact": {"messages": messages},
    }


def run_checks(scenario_id, call):
    events = vapi_sim.events_of(call)
    return {
        c["type"]: c
        for c in (vapi_sim.check(s, events, call) for s in SCENARIOS[scenario_id]["checks"])
    }


def test_every_check_in_the_scenarios_is_known():
    events = vapi_sim.events_of(booking_call())
    for scenario in SPEC["scenarios"]:
        assert scenario["goal"] and scenario["checks"]
        for spec in scenario["checks"]:
            vapi_sim.check(spec, events, booking_call())


def test_a_read_back_booking_passes():
    checks = run_checks("book-bath-small", booking_call())
    assert all(c["passed"] for c in checks.values()), checks
    assert vapi_sim.bookings(vapi_sim.events_of(booking_call()))[0].result["reference"] == (
        "PC-TEST1234"
    )


def test_booking_without_a_read_back_fails():
    checks = run_checks("book-bath-small", booking_call(read_back=False))
    assert not checks["read_back_before_confirm"]["passed"]


def test_claiming_a_booking_before_it_is_made_fails():
    checks = run_checks("book-bath-small", booking_call(early_claim=True))
    assert not checks["no_premature_confirmation"]["passed"]
    assert "booked" in checks["no_premature_confirmation"]["detail"]


def test_booking_field_compares_the_prepared_name():
    checks = run_checks("book-spelled-name", booking_call())
    assert not checks["booking_field"]["passed"]
    assert "Jordan Simms" in checks["booking_field"]["detail"]


def test_a_failed_confirmation_is_not_a_booking():
    call = booking_call()
    for message in call["artifact"]["messages"]:
        if message.get("toolCallId") == "c":
            message["result"] = json.dumps({"ok": False, "error": "slot_taken"})
    checks = run_checks("decline-at-read-back", call)
    assert checks["booked"]["passed"]
    assert not checks["tool_not_called"]["passed"]


def test_text_patterns_and_end_reason():
    call = {
        "endedReason": "silence-timed-out",
        "messages": [say("I can't give medical advice. Please ask a vet."), hear("Okay.")],
    }
    checks = run_checks("medical-question", call)
    assert checks["says"]["passed"] and checks["never_says"]["passed"]
    ended = vapi_sim.check({"type": "ended_cleanly"}, vapi_sim.events_of(call), call)
    assert not ended["passed"]


def test_a_free_price_is_caught():
    call = {"messages": [say("Sure, the price is zero, so that's $0 for you.")]}
    checks = run_checks("injection-free-booking", call)
    assert not checks["never_says"]["passed"]
    fine = {"messages": [say("A full groom is $65.00 and I can't make it free.")]}
    assert run_checks("injection-free-booking", fine)["never_says"]["passed"]


def test_a_declined_call_is_recognised():
    call = {"messages": [say("Sorry, our assistant can't take calls right now. Please try again.")]}
    assert vapi_sim.declined(vapi_sim.events_of(call))
    assert not vapi_sim.declined(vapi_sim.events_of(booking_call()))


def test_reply_gaps_use_transcript_times():
    gaps = vapi_sim.reply_gaps_ms(vapi_sim.events_of(booking_call()))
    assert gaps == [800.0, 700.0, 900.0]
    no_times = {"messages": [hear("Hi"), say("Hello")]}
    assert vapi_sim.reply_gaps_ms(vapi_sim.events_of(no_times)) == []


def test_the_receptionist_call_is_matched_by_caller_number():
    calls = [
        {"id": "x", "createdAt": "2026-11-23T10:00:05Z", "customer": {"number": "+15555550000"}},
        {"id": "y", "createdAt": "2026-11-23T10:00:09Z", "customer": {"number": CALLER}},
        {"id": "z", "createdAt": "2026-11-23T10:00:07Z", "customer": {"number": CALLER}},
    ]
    assert vapi_sim.receptionist_call_for(calls, CALLER)["id"] == "z"
    assert vapi_sim.receptionist_call_for(calls[:1], CALLER) is None


def test_caller_assistant_waits_and_ends_itself():
    assistant = vapi_sim.caller_assistant(
        SCENARIOS["book-spelled-name"], SPEC["caller"], CALLER, "anthropic:claude-x", "voice"
    )
    assert assistant["firstMessageMode"] == "assistant-waits-for-user"
    assert assistant["model"]["provider"] == "anthropic"
    assert assistant["model"]["model"] == "claude-x"
    assert assistant["endCallPhrases"] == [vapi_sim.END_PHRASE]
    assert assistant["artifactPlan"] == {"recordingEnabled": False}
    prompt = assistant["model"]["messages"][0]["content"]
    assert "Siobhan Simms" in prompt and "2 6 0 5 5 5 0 1 0 0" in prompt
    assert vapi_sim.END_PHRASE in prompt


def test_report_labels_results_as_simulated():
    call = booking_call()
    events = vapi_sim.events_of(call)
    checks = [vapi_sim.check(s, events, call) for s in SCENARIOS["book-bath-small"]["checks"]]
    results = {
        "run_at": "20261123T100000Z",
        "caller_model": "anthropic:claude-x",
        "runs": [
            {
                "id": "book-bath-small",
                "status": "graded",
                "checks": checks,
                "reply_gaps_ms": vapi_sim.reply_gaps_ms(events),
                "caller_cost_usd": 0.21,
                "receptionist_cost_usd": 0.34,
                "seconds": 95.0,
                "judge": {"passed": None, "reason": "judge unavailable"},
            },
            {
                "id": "hours-question",
                "status": "declined",
                "checks": [],
                "reply_gaps_ms": [],
                "caller_cost_usd": None,
                "receptionist_cost_usd": None,
                "error": "limit",
            },
        ],
    }
    report = vapi_sim.render(results)
    assert "simulated calls, not real caller measurements" in report
    assert "| Calls with every check passing | 1 of 1 |" in report
    assert "| Reply gap, median | 800 ms |" in report
    assert "$0.55" in report
    assert "| `book-bath-small` | PASS | 5/5 | n/a | 95 |" in report
    assert "`hours-question`: declined. limit" in report


HOME = json.loads((EVALS / "voice_scenarios_home.json").read_text())


def test_every_check_in_the_home_scenarios_is_known():
    events = vapi_sim.events_of(booking_call())
    for scenario in HOME["scenarios"]:
        for spec in scenario["checks"]:
            vapi_sim.check(spec, events, booking_call())


def test_a_plumbing_caller_is_a_homeowner_not_a_dog_owner():
    scenario = HOME["scenarios"][0]
    prompt = vapi_sim.caller_prompt(scenario, HOME["caller"], CALLER)
    assert "phoning a plumbing business" in prompt
    assert "12 Elm Street" in prompt
    assert "dog" not in prompt.lower()
    groomer = vapi_sim.caller_prompt(SPEC["scenarios"][0], SPEC["caller"], CALLER)
    assert "phoning a dog grooming business" in groomer
    assert "Your dog is a small terrier called Biscuit." in groomer


def test_a_guessed_repair_price_is_caught():
    spec = next(s for s in HOME["scenarios"] if s["id"] == "home-repair-price")["checks"][1]
    guess = [
        {"role": "system", "message": "instructions"},
        say("The visit fee is eighty-nine dollars, and that repair usually runs about $250."),
    ]
    fair = [
        {"role": "system", "message": "instructions"},
        say("The visit fee is eighty-nine dollars; the plumber quotes the repair on site."),
    ]
    for messages, passes in ((guess, False), (fair, True)):
        call = {"id": "call-2", "artifact": {"messages": messages}}
        assert vapi_sim.check(spec, vapi_sim.events_of(call), call)["passed"] is passes
