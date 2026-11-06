"""Turns raw results into a Markdown report. Every figure comes from the results file."""

import statistics


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def render(results: dict) -> str:
    runs = results["runs"]
    scenario_pass = [r for r in runs if all(c["passed"] for c in r["checks"])]
    checks = [c for r in runs for c in r["checks"]]
    tool_calls = sum(r["tool_calls"] for r in runs)
    tool_failures = sum(r["tool_failures"] for r in runs)
    latencies = [m for r in runs for m in r["first_output_ms"] if m is not None]
    median = statistics.median(latencies) if latencies else None
    tokens_in = sum(r["input_tokens"] for r in runs)
    tokens_out = sum(r["output_tokens"] for r in runs)
    by_category: dict[str, list[dict]] = {}
    for run in runs:
        by_category.setdefault(run["category"], []).append(run)

    lines = [
        "# Scripted evaluation report",
        "",
        "**This is a scripted text evaluation, not a voice measurement.** The assistant ran "
        "with the same model, instructions, and tools as live voice, but in text mode, against "
        "an evaluation "
        "database with the clock fixed to "
        f"{results['today']}. Text checks are heuristics: read the transcript of any failure.",
        "",
        f"- Model: `{results['model']}`",
        f"- Run at: {results['run_at']}",
        f"- Scenarios: {len(runs)} runs of {results['scenario_count']} scenarios",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Scenarios with every check passing | {len(scenario_pass)} of {len(runs)} "
        f"({100 * len(scenario_pass) / max(len(runs), 1):.0f}%) |",
        f"| Checks passing | {sum(c['passed'] for c in checks)} of {len(checks)} |",
        f"| Tool calls | {tool_calls}, of which {tool_failures} returned an error |",
        f"| Time to first output, median | {fmt(median)} |",
        f"| Time to first output, 90th percentile | {fmt(percentile(latencies, 0.9))} |",
        f"| Tokens | {tokens_in} input, {tokens_out} output |",
        "",
        "Time to first output is measured from sending `response.create` to the first text or tool "
        "argument event over the WebSocket, from the machine running the evaluation. It is not "
        "spoken-audio latency.",
        "",
        "Tool errors include deliberate ones, such as scenarios that simulate a backend failure or "
        "ask for an unsupported date.",
        "",
        "## By category",
        "",
        "| Category | Passing |",
        "|---|---|",
    ]
    for category, items in sorted(by_category.items()):
        passing = sum(all(c["passed"] for c in r["checks"]) for r in items)
        lines.append(f"| {category} | {passing} of {len(items)} |")
    lines += ["", "## Failures", ""]
    failures = [(r, c) for r in runs for c in r["checks"] if not c["passed"]]
    if not failures:
        lines.append("None.")
    for run, failed in failures:
        lines.append(f"- `{run['id']}`: {failed['type']}. {failed['detail']}")
    lines += [
        "",
        "## Not covered here",
        "",
        "- Microphone permission, audio, interruption mid-sentence, and echo are voice behaviours. "
        "Microphone denial and connection failures are covered by automated frontend tests. Audio "
        "quality and latency need manual calls.",
        "",
    ]
    return "\n".join(lines)


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} ms"
