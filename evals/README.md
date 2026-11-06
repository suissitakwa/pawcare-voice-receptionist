# Evaluations

Three kinds of evidence are kept apart and are never reported as one another:

| Layer | Location | Measures |
|---|---|---|
| Automated tests | `backend/tests/`, `frontend/src/*.test.tsx` | Booking rules, concurrency, tools, and the voice interface, with no model involved |
| Scripted evaluations | `evals/` | The assistant's decisions in text conversations, using the real model, instructions, and tools |
| Manual calls | `docs/progress.md` | Real spoken calls, logged as individual observations |

**Scripted evaluation results are not voice measurements.** They run in text mode. They show what the model decides, such as which tool it calls, what it claims, and whether it offers real times. They do not measure speech recognition, audio quality, interruption mid-sentence, or spoken latency.

## Scenarios

`scenarios.json` holds 34 scripted conversations. The clock is fixed to Monday 4 January 2027, so availability is the same on every run.

| Category | Covers |
|---|---|
| `faq` | Approved answers, and saying "I don't know" for anything not approved |
| `pricing` | Exact demo prices and durations, and asking for the dog's size first |
| `booking` | Full bookings, closed days, and a caller saying "book it" instead of pressing Confirm |
| `change` | Changing the service or date, including after a summary is shown |
| `correction` | Corrected name, phone, or email |
| `interruption` | Abandoning or switching topic mid-flow. Real barge-in needs a voice call |
| `unsupported` | Cats, dates beyond the booking window, and medical advice |
| `conflict` | A slot taken by another customer before preparing or before confirming |
| `failure` | The booking system unreachable during a call |
| `callback` | Extra-large dogs, behaviour concerns, and asking for a person |
| `safety` | Prompt injection asking for a free booking or a false price |

Microphone denial is a browser behaviour, not a model decision. It is covered by `frontend/src/VoiceAssistant.test.tsx`.

## Checks

Each scenario lists checks that run against the transcript, the tool calls, and the evaluation database. These include:

- `no_premature_confirmation`: the assistant never claims a booking before the visitor's click succeeds
- `offered_times_valid` and `max_times_offered`: offered times came from `check_availability`, with at most three in one reply
- `read_back_before_prepare`: contact details were read back before preparing
- `bookings`, `callback`, `tool_called`, and `tool_not_called`, checked against database state and the tool log

Text checks are heuristics. Each result records its evidence, so a failure means "read this transcript", not automatic proof of a fault.

## Running

You need the local database (`docker compose up -d db`) and an OpenAI key. Put the key in the environment for one command only, never in a file:

```bash
cd backend
OPENAI_API_KEY=your-key uv run python ../evals/run.py
```

Options: `--scenario <id>`, repeatable, to run some scenarios only, and `--repeat N` to run each N times, which shows how consistent the model is.

The runner uses its own `pawcare_eval` database and refuses any database whose name does not end with `_eval`. A run makes about 100 short text requests to the model, which costs a small amount on your OpenAI account.

Results are written to `evals/results/<timestamp>.json` and `evals/results/<timestamp>.md`.

## Offline self-tests

`backend/tests/test_evals.py` runs the harness and checks with a scripted fake model, so CI verifies the evaluation code itself without calling a provider.
