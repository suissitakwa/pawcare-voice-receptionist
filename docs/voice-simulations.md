# Simulated phone calls

`evals/voice_sim.py` phones the receptionist with a scripted caller and grades each call. The caller is a second Vapi assistant. It dials the receptionist's real number from a second Twilio number, so everything on the receptionist side is real: Twilio, Vapi's speech recognition and voice, turn-taking, and this backend's instructions and tools.

**These are simulated calls, not real caller measurements.** The caller is a text-to-speech voice in a quiet room, with no accent, background noise or real hesitation. Reports say so, and their figures are never presented as real voice measurements. The manual script in [voice-testing.md](voice-testing.md) is still needed for noise, echo, barge-in and real voices.

| Layer | Speech | Tools and database | Caller |
|---|---|---|---|
| Scripted evaluations ([evals/](../evals/README.md)) | None, text mode | Evaluation database | Scripted text |
| Simulated calls (this page) | Real, over the phone | Live backend | Synthetic voice |
| Manual calls ([voice-testing.md](voice-testing.md)) | Real | Live backend | A person |

## How a run works

```
voice_sim.py ──POST /call──► Vapi caller assistant ──dials from the test number──► receptionist number
                                                                                       │
                                                         Vapi receptionist ◄──tools──► backend
voice_sim.py ◄──GET /call── both calls: transcript, tool calls and results, cost, end reason
     └─► checks, optional model judge, cancels test bookings, writes the report
```

1. For each scenario in `evals/voice_scenarios.json`, the runner creates an outbound Vapi call with a caller assistant made for it on the spot. The caller waits for the greeting, follows its goal, and hangs up by saying "That's all, goodbye." It is not recorded.
2. When the caller's call ends, the runner finds the receptionist's side among the calls on the receptionist's number, by the caller's number, and waits for its transcript.
3. It grades the receptionist's side. That side has the tool calls and the backend's results, so the checks use what the backend actually did, not what was said.
4. Test bookings are cancelled through the staff API, if staff sign-in details are given. Otherwise the runner prints the references to cancel by hand.

## Checks

| Check | Passes when |
|---|---|
| `booked` | A `confirm_booking` succeeded (or didn't, with `"expected": false`) |
| `booking_field` | The booking's `owner_name` or `pet_name`, from the last `prepare_booking` before it, equals the expected value |
| `read_back_before_confirm` | Between `prepare_booking` and `confirm_booking`, the receptionist spoke, the caller answered, and `caller_confirmed` was true |
| `no_premature_confirmation` | The receptionist didn't say the caller was booked before a booking succeeded |
| `tool_called`, `tool_not_called` | The named tool was or wasn't used |
| `callback` | A callback request was made |
| `says`, `never_says` | A pattern does or doesn't appear in what the receptionist said |
| `ended_cleanly` | Vapi says either side hung up normally |

Text checks are heuristics: read the transcript of any failure. With `--judge`, a model also reads each transcript against the scenario's rubric. That is a second opinion, not proof.

A call the backend turns away (for example over the per-caller limit) is reported as **declined**, not failed.

The **reply gap** is from the end of a caller turn to the start of the receptionist's next turn, by Vapi's transcript timestamps. It includes tool calls, is approximate, and comes from a synthetic caller.

## Setup

1. **A second Twilio number** for the caller. Buy one in Twilio and import it in the Vapi dashboard (**Phone Numbers → Import → Twilio**). Leave its assistant and server URL empty. The caller can't phone from the receptionist's own number.
2. **Phone number ids.** In Vapi, open each number and copy its id: the new caller number, and the receptionist's number from [vapi.md](vapi.md). Both must be in the same Vapi account as your API key.
3. **Your Vapi private API key**, from **Vapi → Organization settings → API keys**.
4. **Per-caller limit.** The backend accepts 4 calls an hour from one number (`PHONE_CALLS_PER_CALLER_PER_HOUR`). For a full run of 10 scenarios, raise it on Railway for the test and set it back afterwards.

Put the keys in the environment for one command only, never in a file.

## Running

Without `--yes`, nothing is called: the runner looks up both numbers and says what it would do.

```bash
cd backend
VAPI_API_KEY=... SIM_CALLER_NUMBER_ID=... SIM_RECEPTIONIST_NUMBER_ID=... \
  uv run python ../evals/voice_sim.py --scenario book-bath-small --yes
```

Options:

| Option | Meaning |
|---|---|
| `--scenario <id>` | Run only this scenario; repeatable |
| `--caller-model provider:model` | The caller's model in Vapi. Default `anthropic:claude-haiku-4-5-20251001` |
| `--caller-voice <id>` | The caller's ElevenLabs voice. Default "Adam", unlike the receptionist's default |
| `--judge` | Also grade with a model rubric; needs `OPENAI_API_KEY` |
| `--gap <seconds>` | Wait between calls, default 20 |
| `--scenarios <file>` | Scenario file in `evals/`: `voice_scenarios.json` (the groomer, default) or `voice_scenarios_home.json` (the plumber) |

## The plumbing demo

`evals/voice_scenarios_home.json` has 4 calls to the fictional Riverbend Plumbing (`plumbing-demo`): a leak booked in an arrival window, a caller pushing for a repair price, a gas smell, and a burst pipe that should become an emergency callback. The caller plays a homeowner at 12 Elm Street, San Jose, instead of a dog owner. The receptionist number must ring `plumbing-demo`, so register it first with `python -m app.phone_numbers add plumbing-demo <number>` (a second Vapi number, or the demo line moved over for the test), then run with `--scenarios voice_scenarios_home.json`.

To cancel test bookings automatically, add `PAWCARE_API_URL` (the backend's https address), `STAFF_EMAIL` and `STAFF_PASSWORD` for a staff account.

Results go to `evals/results/voice/<timestamp>.json` and `.md`. That folder is gitignored, because it holds the test phone numbers and transcripts. Copy the summary table into [progress.md](progress.md) under a dated heading, labelled as simulated calls.

## Costs

Each scenario is a real phone call on both sides: Vapi minutes for the caller and the receptionist, their transcriber, model and voice providers, and Twilio minutes on both numbers. A booking call takes about two minutes. The report adds up the cost Vapi reports for both sides. Try one scenario first.

## Offline tests

`backend/tests/test_voice_sims.py` runs the checks, the call parsing, the caller assistant and the report on sample Vapi calls, without calling Vapi.
