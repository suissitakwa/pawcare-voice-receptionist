"""Place simulated phone calls to the receptionist through Vapi and grade them.

Each scenario is one real phone call, so each costs Vapi and Twilio minutes on both sides.
Nothing is called without --yes. See docs/voice-simulations.md for setup.

From backend/, with the keys in the environment for this command only:

    VAPI_API_KEY=... SIM_CALLER_NUMBER_ID=... SIM_RECEPTIONIST_NUMBER_ID=... \
        uv run python ../evals/voice_sim.py --scenario book-bath-small --yes

Writes evals/results/voice/<timestamp>.json and .md, which are gitignored because they hold
the test numbers and transcripts.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pawcare_evals import vapi_sim  # noqa: E402

CALLER_MODEL = "anthropic:claude-haiku-4-5-20251001"
CALLER_VOICE = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs "Adam", unlike the receptionist's default
JUDGE_MODEL = "gpt-4.1-mini"
POLL_SECONDS = 5


def wait_for_end(client: vapi_sim.VapiClient, call_id: str, limit_seconds: int) -> dict:
    deadline = time.monotonic() + limit_seconds
    while True:
        call = client.call(call_id)
        if call.get("status") == "ended" or time.monotonic() > deadline:
            return call
        time.sleep(POLL_SECONDS)


def find_receptionist_call(
    client: vapi_sim.VapiClient, number_id: str, caller_number: str, since: str
) -> dict | None:
    """The receptionist's side of the call. Its transcript and tool calls can arrive a little
    after the caller's side ends, so wait for it to end too."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        found = vapi_sim.receptionist_call_for(client.calls(number_id, since), caller_number)
        if found is not None:
            full = client.call(found["id"])
            if full.get("status") == "ended" and vapi_sim.messages_of(full):
                return full
        time.sleep(POLL_SECONDS)
    return None


def seconds_of(call: dict) -> float | None:
    try:
        started = datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return None
    return (ended - started).total_seconds()


def cancel_bookings(references: list[str]) -> list[str]:
    """Cancel the test bookings through the staff API, if staff details are in the
    environment. Returns the references still to cancel by hand."""
    base = os.environ.get("PAWCARE_API_URL", "").rstrip("/")
    email = os.environ.get("STAFF_EMAIL", "")
    password = os.environ.get("STAFF_PASSWORD", "")
    if not references or not (base and email and password):
        return references

    def post(path: str, body: dict | None, token: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with vapi_sim.open_https("POST", base + path, headers, body or {}, 30) as response:
            return json.loads(response.read() or b"{}")

    try:
        token = post("/api/v1/staff/login", {"email": email, "password": password})["token"]
    except (urllib.error.URLError, KeyError, ValueError) as error:
        print(f"Could not sign in to cancel test bookings: {error}")
        return references
    left = []
    for reference in references:
        try:
            post(f"/api/v1/staff/bookings/{reference}/cancel", None, token)
            print(f"Cancelled test booking {reference}")
        except (urllib.error.URLError, ValueError) as error:
            print(f"Could not cancel {reference}: {error}")
            left.append(reference)
    return left


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", help="Run only these scenario ids")
    parser.add_argument("--caller-model", default=CALLER_MODEL, help="provider:model in Vapi")
    parser.add_argument("--caller-voice", default=CALLER_VOICE, help="ElevenLabs voice id")
    parser.add_argument("--judge", action="store_true", help="Also grade with a model rubric")
    parser.add_argument("--gap", type=int, default=20, help="Seconds to wait between calls")
    parser.add_argument("--yes", action="store_true", help="Place the calls (they cost money)")
    parser.add_argument(
        "--scenarios",
        default="voice_scenarios.json",
        help="Scenario file in evals/: voice_scenarios.json (the groomer) or "
        "voice_scenarios_home.json (the plumber); the receptionist number must ring that business",
    )
    args = parser.parse_args()

    needed = ["VAPI_API_KEY", "SIM_CALLER_NUMBER_ID", "SIM_RECEPTIONIST_NUMBER_ID"]
    missing = [name for name in needed if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} in the environment for this command only.")
    judge_key = os.environ.get("OPENAI_API_KEY", "")
    if args.judge and not judge_key:
        sys.exit("--judge needs OPENAI_API_KEY in the environment.")

    spec = json.loads((HERE / args.scenarios).read_text())
    scenarios = [s for s in spec["scenarios"] if not args.scenario or s["id"] in args.scenario]
    if not scenarios:
        sys.exit("No matching scenarios.")

    client = vapi_sim.VapiClient(os.environ["VAPI_API_KEY"])
    caller_id = os.environ["SIM_CALLER_NUMBER_ID"]
    receptionist_id = os.environ["SIM_RECEPTIONIST_NUMBER_ID"]
    try:
        caller_number = client.phone_number(caller_id)["number"]
        receptionist_number = client.phone_number(receptionist_id)["number"]
    except (vapi_sim.VapiError, KeyError) as error:
        sys.exit(f"Could not look up the phone numbers in Vapi: {error}")

    print(f"{len(scenarios)} simulated calls from {caller_number} to {receptionist_number}.")
    print("Each is a real call billed by Vapi and Twilio on both sides, a few minutes each.")
    if len(scenarios) > 4:
        print(
            "The backend allows PHONE_CALLS_PER_CALLER_PER_HOUR calls from one number (4 by "
            "default). Raise it on Railway for the test, or later calls will be turned away."
        )
    if not args.yes:
        sys.exit("Nothing called. Run again with --yes to place the calls.")

    runs = []
    references: list[str] = []
    for position, scenario in enumerate(scenarios):
        if position:
            time.sleep(args.gap)
        run = {
            "id": scenario["id"],
            "category": scenario["category"],
            "status": "graded",
            "checks": [],
            "reply_gaps_ms": [],
            "caller_cost_usd": None,
            "receptionist_cost_usd": None,
        }
        runs.append(run)
        assistant = vapi_sim.caller_assistant(
            scenario, spec["caller"], caller_number, args.caller_model, args.caller_voice
        )
        since = (datetime.now(UTC) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        try:
            placed = client.create_call(assistant, caller_id, receptionist_number)
            caller_call = wait_for_end(client, placed["id"], assistant["maxDurationSeconds"] + 120)
            run["caller_cost_usd"] = vapi_sim.cost_of(caller_call)
            run["caller_ended"] = caller_call.get("endedReason")
            receptionist_call = find_receptionist_call(
                client, receptionist_id, caller_number, since
            )
        except (vapi_sim.VapiError, KeyError) as error:
            run["status"], run["error"] = "error", str(error)
            print(f"ERROR {scenario['id']}: {error}")
            continue
        if receptionist_call is None:
            run["status"] = "no receptionist call"
            run["error"] = "Vapi has no inbound call from the caller number for this run."
            print(f"ERROR {scenario['id']}: {run['error']}")
            continue

        events = vapi_sim.events_of(receptionist_call)
        lines = vapi_sim.transcript(events)
        run["transcript"] = lines
        run["receptionist_call_id"] = receptionist_call.get("id")
        run["receptionist_cost_usd"] = vapi_sim.cost_of(receptionist_call)
        run["seconds"] = seconds_of(receptionist_call)
        references += [e.result["reference"] for e in vapi_sim.bookings(events)]
        if vapi_sim.declined(events):
            run["status"] = "declined"
            run["error"] = "The backend turned the call away, likely the per-caller limit."
            print(f"SKIP  {scenario['id']}: {run['error']}")
            continue
        run["checks"] = [vapi_sim.check(c, events, receptionist_call) for c in scenario["checks"]]
        run["reply_gaps_ms"] = vapi_sim.reply_gaps_ms(events)
        if args.judge and scenario.get("rubric"):
            run["judge"] = vapi_sim.judge(
                judge_key,
                JUDGE_MODEL,
                scenario["rubric"],
                lines,
                vapi_sim.business_kind(spec["caller"]),
            )
        passed = all(c["passed"] for c in run["checks"])
        print(f"{'PASS' if passed else 'FAIL'}  {scenario['id']}")
        for c in run["checks"]:
            if not c["passed"]:
                print(f"      {c['type']}: {c['detail']}")

    left = cancel_bookings(references)
    if left:
        print(f"Cancel these test bookings from the staff dashboard: {', '.join(left)}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results = {
        "run_at": stamp,
        "caller_model": args.caller_model,
        "judge_model": JUDGE_MODEL if args.judge else None,
        "test_bookings": references,
        "test_bookings_left": left,
        "runs": runs,
    }
    out = HERE / "results" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}.json").write_text(json.dumps(results, indent=2, default=str))
    (out / f"{stamp}.md").write_text(vapi_sim.render(results))
    print(f"\nReport: evals/results/voice/{stamp}.md")


if __name__ == "__main__":
    main()
