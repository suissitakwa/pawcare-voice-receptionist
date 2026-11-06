"""Deterministic checks over a scenario record and the database.

Text checks are heuristics on the assistant's words, so each result carries the evidence it
was based on. A failed heuristic is a prompt to read the transcript, not proof of a fault.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, CallbackRequest

LA = ZoneInfo("America/Los_Angeles")
TIME = re.compile(r"\b(1[0-2]|[1-9])(?::([0-5]\d))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)", re.IGNORECASE)
CLAIMS_BOOKED = re.compile(
    r"(booking is (now )?(confirmed|complete|booked))|(you'?re (all )?booked)|"
    r"(you are (all )?booked)|(has been (booked|confirmed))|(is (now )?(booked|confirmed))|"
    r"(confirmed your (booking|appointment))|(successfully booked)",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(not|n't|until|once|after you|when you|before you|isn't|yet)\b", re.IGNORECASE
)


def normalize_time(match: re.Match) -> str:
    hour, minute, meridiem = match.group(1), match.group(2), match.group(3)
    suffix = "AM" if meridiem.lower().startswith("a") else "PM"
    return (
        f"{int(hour)} {suffix}"
        if not minute or minute == "00"
        else f"{int(hour)}:{minute} {suffix}"
    )


def times_in(text: str) -> list[str]:
    return [normalize_time(m) for m in TIME.finditer(text)]


def assistant(record) -> list[tuple[int, dict]]:
    return [(i, e) for i, e in enumerate(record.entries) if e["kind"] == "assistant"]


def availability_labels(record) -> set[str]:
    labels = set()
    for call in record.tool_calls:
        if call["name"] == "check_availability" and call["ok"]:
            labels.update(t["label"] for t in call["result"].get("times", []))
    return labels


def args_match(actual: dict, expected: dict) -> bool:
    for key, value in expected.items():
        if str(actual.get(key, "")).strip().lower() != str(value).strip().lower():
            return False
    return True


def digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper().replace("DASH", ""))


def prepares(record) -> list[dict]:
    return [c for c in record.tool_calls if c["name"] == "prepare_booking"]


def check(record, spec: dict, session: Session) -> tuple[bool, str]:
    kind = spec["type"]
    said = [e["text"] for _, e in assistant(record)]
    joined = " ".join(said).lower()

    if kind == "mentions_any":
        hit = next((v for v in spec["values"] if v.lower() in joined), None)
        return hit is not None, f"found {hit!r}" if hit else "none of the phrases appeared"

    if kind == "not_mentions":
        hit = next((v for v in spec["values"] if v.lower() in joined), None)
        return hit is None, f"said {hit!r}" if hit else "none appeared"

    if kind == "tool_called":
        calls = [c for c in record.tool_calls if c["name"] == spec["name"]]
        matched = [c for c in calls if args_match(c["args"], spec.get("args", {}))]
        return bool(matched), f"{len(calls)} call(s), {len(matched)} matching"

    if kind == "tool_not_called":
        calls = [c for c in record.tool_calls if c["name"] == spec["name"]]
        return not calls, f"{len(calls)} call(s)"

    if kind == "tool_called_after_conflict":
        if record.conflict_at is None:
            return False, "no conflict happened"
        later = [
            c
            for c in record.tool_calls
            if c["name"] == spec["name"] and c["index"] > record.conflict_at
        ]
        return bool(later), f"{len(later)} call(s) after the conflict"

    if kind == "bookings":
        total = session.scalar(
            select(func.count()).select_from(Booking).where(Booking.status == "confirmed")
        )
        caller = session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.status == "confirmed", Booking.voice_session_id == record.voice_session_id
            )
        )
        ok = total == spec["count"] and ("of_caller" not in spec or caller == spec["of_caller"])
        return ok, f"{total} confirmed, {caller} from this conversation"

    if kind == "callback":
        rows = session.scalars(select(CallbackRequest)).all()
        if "reason" in spec:
            rows = [r for r in rows if r.reason == spec["reason"]]
        return bool(rows), f"{len(rows)} matching callback(s)"

    if kind == "offered_times_valid":
        allowed = availability_labels(record)
        bad = []
        for _, entry in assistant(record):
            if "check_availability" in entry.get("after_tools", []):
                bad += [t for t in times_in(entry["text"]) if t not in allowed]
        return (
            not bad,
            f"times not returned by the tool: {sorted(set(bad))}"
            if bad
            else "all offered times were real",
        )

    if kind == "max_times_offered":
        worst = 0
        for _, entry in assistant(record):
            if "check_availability" in entry.get("after_tools", []):
                worst = max(worst, len(set(times_in(entry["text"]))))
        return worst <= spec["count"], f"at most {worst} time(s) in one reply"

    if kind == "no_invented_times":
        failed_after = [
            e
            for _, e in assistant(record)
            if e.get("after_tools") and any(not c["ok"] for c in record.tool_calls)
        ]
        bad = [t for e in failed_after for t in times_in(e["text"])]
        return not bad, f"mentioned {bad}" if bad else "no times mentioned after the failure"

    if kind == "no_premature_confirmation":
        limit = record.confirm_at if record.confirm_at is not None else len(record.entries)
        for index, entry in assistant(record):
            if index >= limit:
                break
            for sentence in re.split(r"(?<=[.!?])\s+", entry["text"]):
                if CLAIMS_BOOKED.search(sentence) and not NEGATION.search(sentence):
                    return False, f"claimed booked before the click: {sentence!r}"
        return True, "no booking claim before a successful click"

    if kind == "reference_spoken":
        if not record.references:
            return False, "nothing was confirmed"
        spoken = compact(" ".join(said))
        missing = [r for r in record.references if compact(r) not in spoken]
        return not missing, f"missing {missing}" if missing else "reference read out"

    if kind == "read_back_before_prepare":
        first = next((c["index"] for c in prepares(record) if c["ok"]), None)
        if first is None:
            return False, "no booking was prepared"
        before = " ".join(e["text"] for i, e in assistant(record) if i < first)
        missing = [
            v for v in spec["values"] if v not in digits(before) and v.lower() not in before.lower()
        ]
        return not missing, f"not read back: {missing}" if missing else "read back before preparing"

    if kind == "last_prepare_args":
        made = [c for c in prepares(record) if c["ok"]]
        if not made:
            return False, "no booking was prepared"
        return args_match(made[-1]["args"], spec["args"]), f"last arguments {made[-1]['args']}"

    if kind == "last_prepare_args_contains":
        made = [c for c in prepares(record) if c["ok"]]
        if not made:
            return False, "no booking was prepared"
        value = str(made[-1]["args"].get(spec["field"], ""))
        wanted = spec["value"]
        ok = wanted.lower() in value.lower() or (digits(wanted) and digits(wanted) in digits(value))
        return bool(ok), f"{spec['field']}={value!r}"

    if kind == "never_prepared_with":
        bad = [
            c
            for c in prepares(record)
            if c["ok"] and spec["value"].lower() in str(c["args"].get(spec["field"], "")).lower()
        ]
        return not bad, f"{len(bad)} summary(s) used {spec['value']!r}"

    if kind == "last_prepare_args_starts_at":
        made = [c for c in prepares(record) if c["ok"]]
        if not made:
            return False, "no booking was prepared"
        local = datetime.fromisoformat(made[-1]["args"]["starts_at"]).astimezone(LA)
        label = normalize_time(TIME.search(local.strftime("%I:%M %p")))
        return label == spec["time"], f"prepared for {label}"

    if kind == "prepare_count_at_least":
        made = [c for c in prepares(record) if c["ok"]]
        return len(made) >= spec["count"], f"{len(made)} summary(s) prepared"

    if kind == "last_prepare_args_starts_on":
        made = [c for c in prepares(record) if c["ok"]]
        if not made:
            return False, "no booking was prepared"
        starts = datetime.fromisoformat(made[-1]["args"]["starts_at"]).astimezone(LA).date()
        return starts.isoformat() == spec["date"], f"prepared for {starts}"

    raise ValueError(f"Unknown check type: {kind}")


def evaluate(record, scenario: dict, session: Session) -> list[dict]:
    results = []
    if record.error:
        results.append({"type": "completed", "passed": False, "detail": record.error})
    for spec in scenario["checks"]:
        passed, detail = check(record, spec, session)
        results.append({"type": spec["type"], "spec": spec, "passed": passed, "detail": detail})
    return results
