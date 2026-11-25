"""Simulated phone calls through Vapi.

A second Vapi assistant plays a scripted caller and phones the receptionist's number. The
receptionist side is the real phone setup: Vapi's speech recognition and voice, this backend's
instructions and tools. Afterwards both calls are fetched from Vapi and graded.

Everything here except `VapiClient` and `judge` is pure, so it can be tested offline.

The caller is synthetic: a text-to-speech voice in a quiet room, with no accent, noise or real
hesitation. Results are simulated calls, never reported as real caller measurements.
"""

import json
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

VAPI_URL = "https://api.vapi.ai"
END_PHRASE = "That's all, goodbye."
DECLINED = re.compile(r"can't take calls right now", re.I)
PREMATURE = re.compile(
    r"\b(you'?re (all )?(booked|set)|(is|are) (now )?(booked|confirmed)|booking is confirmed"
    r"|i'?ve (booked|confirmed)|all booked)\b",
    re.I,
)
# Vapi's reasons for a call that ended normally, from either side.
CLEAN_ENDS = {"customer-ended-call", "assistant-ended-call", "assistant-said-end-call-phrase"}


def business_kind(caller: dict) -> str:
    """What the receptionist's business is, from the scenario file: a dog groomer unless it
    says otherwise."""
    return caller.get("business_kind", "a dog grooming business")


def caller_prompt(scenario: dict, caller: dict, caller_number: str) -> str:
    owner = scenario.get("owner_name", caller["owner_name"])
    digits = " ".join(ch for ch in caller_number.removeprefix("+1") if ch.isdigit())
    # A dog owner names their dog; anyone else says what the scenario file tells them.
    about = (
        f"Your dog is {caller['pet']} called {caller['pet_name']}. "
        if "pet" in caller
        else (scenario.get("about") or caller.get("about", "")) + " "
    )
    return (
        f"You are role-playing a customer phoning {business_kind(caller)}, as part of a test of "
        "its phone receptionist. You are the caller, never the business. The receptionist "
        "speaks first.\n\n"
        f"Your name is {owner}. {about}If asked for a phone "
        f"number, give {digits}, digit by digit. You have no email address to give.\n\n"
        f"Your goal: {scenario['goal']}\n\n"
        "How to speak: like a real person on the phone. One or two short sentences at a time. "
        "Answer what you're asked and don't volunteer everything at once. Never make up prices, "
        "times or booking references; only use what the receptionist tells you. If you're asked "
        "something your goal doesn't cover, give a simple, reasonable answer.\n\n"
        "When your goal is done, or if you're stuck after three tries, say exactly: "
        f'"{END_PHRASE}"'
    )


def caller_assistant(
    scenario: dict,
    caller: dict,
    caller_number: str,
    model: str,
    voice_id: str,
) -> dict:
    """A transient Vapi assistant that plays the caller. `model` is "provider:model"."""
    provider, _, model_name = model.partition(":")
    return {
        "name": f"Sim caller {scenario['id']}"[:40],
        "firstMessageMode": "assistant-waits-for-user",
        "model": {
            "provider": provider,
            "model": model_name,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": caller_prompt(scenario, caller, caller_number)}
            ],
        },
        "voice": {"provider": "11labs", "voiceId": voice_id},
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"},
        "endCallPhrases": [END_PHRASE],
        "maxDurationSeconds": scenario.get("max_seconds", 300),
        "artifactPlan": {"recordingEnabled": False},
    }


@dataclass
class Event:
    kind: str  # "say" (receptionist speaks), "hear" (caller speaks), "tool"
    index: int
    text: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    result: dict | None = None
    time: float | None = None
    end_time: float | None = None


def messages_of(call: dict) -> list[dict]:
    artifact = call.get("artifact") if isinstance(call.get("artifact"), dict) else {}
    messages = artifact.get("messages") or call.get("messages") or []
    return [m for m in messages if isinstance(m, dict)]


def as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def number(value) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def events_of(call: dict) -> list[Event]:
    """The receptionist's call as events, in order. On that call Vapi's "bot" is the
    receptionist and its "user" is the simulated caller."""
    events: list[Event] = []
    by_id: dict[str, Event] = {}
    for message in messages_of(call):
        role = message.get("role")
        index = len(events)
        if role in ("bot", "assistant") and message.get("message"):
            events.append(
                Event(
                    "say",
                    index,
                    text=str(message["message"]),
                    time=number(message.get("time")),
                    end_time=number(message.get("endTime")),
                )
            )
        elif role == "user" and message.get("message"):
            events.append(
                Event(
                    "hear",
                    index,
                    text=str(message["message"]),
                    time=number(message.get("time")),
                    end_time=number(message.get("endTime")),
                )
            )
        elif role == "tool_calls":
            for tool_call in message.get("toolCalls") or []:
                function = as_dict(tool_call.get("function")) if isinstance(tool_call, dict) else {}
                event = Event(
                    "tool",
                    len(events),
                    name=str(function.get("name") or ""),
                    args=as_dict(function.get("arguments")),
                    time=number(message.get("time")),
                )
                events.append(event)
                if isinstance(tool_call.get("id"), str):
                    by_id[tool_call["id"]] = event
        elif role == "tool_call_result":
            event = by_id.get(message.get("toolCallId"))
            if event is not None:
                event.result = as_dict(message.get("result"))
    return events


def ok(event: Event) -> bool:
    return event.kind == "tool" and bool(event.result and event.result.get("ok"))


def bookings(events: list[Event]) -> list[Event]:
    return [e for e in events if e.name == "confirm_booking" and ok(e)]


def last_prepared(events: list[Event], before: int) -> Event | None:
    prepared = [e for e in events if e.name == "prepare_booking" and ok(e) and e.index < before]
    return prepared[-1] if prepared else None


def receptionist_text(events: list[Event]) -> str:
    return "\n".join(e.text for e in events if e.kind == "say")


def check(spec: dict, events: list[Event], receptionist_call: dict) -> dict:
    kind = spec["type"]
    booked = bookings(events)
    tools = [e.name for e in events if e.kind == "tool"]

    def result(passed: bool, detail: str) -> dict:
        return {"type": kind, "passed": passed, "detail": detail}

    if kind == "booked":
        expected = spec["expected"]
        refs = [e.result.get("reference") for e in booked]
        return result(bool(booked) == expected, f"bookings made: {refs or 'none'}")
    if kind == "booking_field":
        if not booked:
            return result(False, "no booking was made")
        prepared = last_prepared(events, booked[-1].index)
        value = (prepared.args.get(spec["field"]) if prepared else None) or ""
        passed = value.strip().lower() == spec["equals"].lower()
        return result(passed, f"{spec['field']} booked as {value!r}")
    if kind == "read_back_before_confirm":
        if not booked:
            return result(False, "no booking was made")
        confirm = booked[-1]
        prepared = last_prepared(events, confirm.index)
        if prepared is None:
            return result(False, "confirm_booking ran without a successful prepare_booking")
        between = [e for e in events if prepared.index < e.index < confirm.index]
        said = [e for e in between if e.kind == "say"]
        heard = [e for e in between if e.kind == "hear" and said and e.index > said[0].index]
        confirmed = confirm.args.get("caller_confirmed") is True
        passed = bool(said) and bool(heard) and confirmed
        return result(
            passed,
            f"after prepare: {len(said)} receptionist turns, {len(heard)} caller replies, "
            f"caller_confirmed={confirm.args.get('caller_confirmed')}",
        )
    if kind == "no_premature_confirmation":
        cutoff = booked[0].index if booked else len(events)
        early = [
            e for e in events if e.kind == "say" and e.index < cutoff and PREMATURE.search(e.text)
        ]
        return result(not early, f"said too early: {early[0].text!r}" if early else "none")
    if kind == "tool_called":
        return result(spec["name"] in tools, f"tools: {tools or 'none'}")
    if kind == "tool_not_called":
        return result(spec["name"] not in tools, f"tools: {tools or 'none'}")
    if kind == "callback":
        made = any(e.name == "create_callback_request" and ok(e) for e in events)
        return result(made, f"tools: {tools or 'none'}")
    if kind in ("says", "never_says"):
        match = re.search(spec["pattern"], receptionist_text(events), re.I)
        found = match.group(0) if match else None
        passed = bool(match) if kind == "says" else not match
        return result(passed, f"matched {found!r}" if found else "no match")
    if kind == "ended_cleanly":
        reason = receptionist_call.get("endedReason") or ""
        return result(reason in CLEAN_ENDS, f"ended: {reason or 'unknown'}")
    raise ValueError(f"Unknown check {kind}")


def declined(events: list[Event]) -> bool:
    """The backend turned the call away, usually because of the per-caller phone limit."""
    said = [e for e in events if e.kind == "say"]
    return (
        bool(said)
        and bool(DECLINED.search(said[0].text))
        and not any(e.kind == "tool" for e in events)
    )


def reply_gaps_ms(events: list[Event]) -> list[float]:
    """From the end of a caller turn to the start of the next receptionist turn, by Vapi's
    transcript timestamps. Only where Vapi gives both times."""
    gaps = []
    last_heard: Event | None = None
    for event in events:
        if event.kind == "hear":
            last_heard = event
        elif event.kind == "say" and last_heard is not None:
            if last_heard.end_time is not None and event.time is not None:
                gap = event.time - last_heard.end_time
                if 0 <= gap < 60_000:
                    gaps.append(gap)
            last_heard = None
    return gaps


def transcript(events: list[Event]) -> list[dict]:
    lines = []
    for e in events:
        if e.kind == "say":
            lines.append({"who": "receptionist", "text": e.text})
        elif e.kind == "hear":
            lines.append({"who": "caller", "text": e.text})
        else:
            outcome = (e.result or {}).get("error") or ("ok" if ok(e) else "no result")
            lines.append({"who": "tool", "text": f"{e.name} -> {outcome}", "args": e.args})
    return lines


def cost_of(call: dict) -> float | None:
    return number(call.get("cost"))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def fmt_ms(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.0f} ms"


def render(results: dict) -> str:
    runs = results["runs"]
    graded = [r for r in runs if r["status"] == "graded"]
    passed = [r for r in graded if all(c["passed"] for c in r["checks"])]
    gaps = [g for r in graded for g in r["reply_gaps_ms"]]
    costs = [c for r in runs for c in (r["caller_cost_usd"], r["receptionist_cost_usd"]) if c]
    lines = [
        "# Simulated phone call report",
        "",
        "**These are simulated calls, not real caller measurements.** A Vapi assistant played "
        "each caller with a text-to-speech voice and phoned the receptionist's real number. "
        "Speech recognition, the voice, turn-taking and tools are real; the caller is not. Text "
        "checks are heuristics: read the transcript of any failure.",
        "",
        f"- Run at: {results['run_at']}",
        f"- Caller model: `{results['caller_model']}`",
        f"- Scenarios: {len(runs)}",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Calls graded | {len(graded)} of {len(runs)} |",
        f"| Calls with every check passing | {len(passed)} of {len(graded)} |",
        f"| Reply gap, median | {fmt_ms(statistics.median(gaps) if gaps else None)} |",
        f"| Reply gap, 90th percentile | {fmt_ms(percentile(gaps, 0.9))} |",
        f"| Vapi cost, both sides | ${sum(costs):.2f} |",
        "",
        "The reply gap is from the end of a caller turn to the start of the receptionist's next "
        "turn, by Vapi's transcript timestamps, so it includes tool calls. It is approximate and "
        "comes from a synthetic caller.",
        "",
        "## Calls",
        "",
        "| Scenario | Result | Checks | Judge | Seconds |",
        "|---|---|---|---|---|",
    ]
    for r in runs:
        checks = f"{sum(c['passed'] for c in r['checks'])}/{len(r['checks'])}"
        judge = r.get("judge") or {}
        verdict = {True: "pass", False: "fail", None: "n/a"}[judge.get("passed")] if judge else ""
        state = r["status"]
        if state == "graded":
            state = "PASS" if all(c["passed"] for c in r["checks"]) else "FAIL"
        seconds = "" if r.get("seconds") is None else f"{r['seconds']:.0f}"
        lines.append(f"| `{r['id']}` | {state} | {checks} | {verdict} | {seconds} |")
    lines += ["", "## Failures", ""]
    failures = [(r, c) for r in graded for c in r["checks"] if not c["passed"]]
    for r in runs:
        if r["status"] != "graded":
            lines.append(f"- `{r['id']}`: {r['status']}. {r.get('error') or ''}".rstrip())
    for r, c in failures:
        lines.append(f"- `{r['id']}`: {c['type']}. {c['detail']}")
    for r in graded:
        judge = r.get("judge") or {}
        if judge and judge.get("passed") is False:
            lines.append(f"- `{r['id']}`: judge. {judge.get('reason', '')}")
    if lines[-1] == "":
        lines.append("None.")
    return "\n".join(lines) + "\n"


def open_https(method: str, url: str, headers: dict, body: dict | None, timeout: int):
    """Only ever https: the URLs come from constants or the environment."""
    if not url.startswith("https://"):
        raise ValueError(f"Refusing a non-https URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


class VapiError(RuntimeError):
    pass


class VapiClient:
    def __init__(self, api_key: str, base: str = VAPI_URL):
        self.api_key = api_key
        self.base = base

    def request(self, method: str, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with open_https(method, self.base + path, headers, body, 30) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise VapiError(f"Vapi {method} {path} returned {error.code}: {detail}") from None
        except urllib.error.URLError as error:
            raise VapiError(f"Could not reach Vapi: {error.reason}") from None

    def phone_number(self, number_id: str) -> dict:
        return self.request("GET", f"/phone-number/{number_id}")

    def create_call(self, assistant: dict, from_number_id: str, to_number: str) -> dict:
        body = {"assistant": assistant, "phoneNumberId": from_number_id}
        body["customer"] = {"number": to_number}
        return self.request("POST", "/call", body)

    def call(self, call_id: str) -> dict:
        return self.request("GET", f"/call/{call_id}")

    def calls(self, phone_number_id: str, created_at_ge: str) -> list[dict]:
        query = urllib.parse.urlencode(
            {"phoneNumberId": phone_number_id, "createdAtGe": created_at_ge, "limit": 20}
        )
        found = self.request("GET", f"/call?{query}")
        return found if isinstance(found, list) else []


def receptionist_call_for(calls: list[dict], caller_number: str) -> dict | None:
    """The inbound call the simulated caller placed: the earliest one from its number."""
    matching = [
        c
        for c in calls
        if isinstance(c.get("customer"), dict) and c["customer"].get("number") == caller_number
    ]
    return min(matching, key=lambda c: c.get("createdAt") or "") if matching else None


def judge(
    api_key: str,
    model: str,
    rubric: str,
    lines: list[dict],
    kind: str = "a dog grooming business",
) -> dict:
    """A model reads the transcript against the scenario's rubric. A second opinion beside the
    checks, not proof."""
    conversation = "\n".join(f"{line['who']}: {line['text']}" for line in lines)
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You grade a phone call between the receptionist of {kind} and a test "
                    "caller. Lines marked tool are the receptionist's tool calls. Reply with "
                    'JSON: {"passed": true or false, "reason": "one sentence"}.'
                ),
            },
            {"role": "user", "content": f"Rubric: {rubric}\n\nCall:\n{conversation}"},
        ],
    }
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with open_https("POST", url, headers, body, 60) as response:
            content = json.loads(response.read())["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, ValueError) as error:
        return {"passed": None, "reason": f"judge unavailable: {error}"}
    verdict = as_dict(content)
    return {"passed": verdict.get("passed") is True, "reason": str(verdict.get("reason", ""))}
