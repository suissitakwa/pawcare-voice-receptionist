# Progress

Evidence log. Each entry records what was actually run. Anything not run is listed as not verified.

## Foundation (2026-11-04)

**Outcome:** a website shell that renders business details read from PostgreSQL through the API, with health endpoints, migrations, repeatable seed data, tests, and CI configuration.

### Verified

Run on macOS with Python 3.12, Node 22.23.4, and PostgreSQL **14.13** running natively. Compose and CI specify PostgreSQL 17, so 14.13 is not the target version.

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` | Pass |
| `pytest` on real PostgreSQL | 14 passed, including migration up, down, and up again |
| `pytest -W error::DeprecationWarning` | 14 passed, no deprecation warnings |
| `npm run typecheck` | Pass |
| `npm test` | 5 passed |
| `npm run build` | Pass: 224.93 kB JS (70.30 kB gzip) |
| `docker compose config` | Valid. This parses the file and does not start anything |
| `GET /health/ready` | 200, `{"status":"ready","database":"ok","schema_revision":"0001"}` |
| `GET /api/v1/business` | 200, record read from PostgreSQL |
| Browser at 800 px and 375 px wide | Renders database content, no horizontal scroll |
| Keyboard | First Tab reveals and focuses the skip link |

**Real database outage,** by stopping PostgreSQL while the app ran:

| Check | Result |
|---|---|
| `GET /health` | 200: liveness does not depend on the database |
| `GET /health/ready` | 503, `database: unreachable`, no connection details |
| `GET /api/v1/business` | 503, `database_unavailable` |
| Browser | Error message and retry button. The demo notice stays visible |
| After restarting PostgreSQL | Readiness returned 200 and the retry button restored the page, without restarting the backend |

### Verified later

The Docker items that could not run at first passed once the machine had recovered. See the next entry.

### Open issue: one unexplained 503

The first page load in the browser got a 503 from `/api/v1/business`, and a reload succeeded. It has not recurred, but the attempt to reproduce it was inconclusive because the machine was too overloaded for the requests to complete.

- **Observed:** the server logged `OperationalError`. PostgreSQL's log showed no error. The machine's load average was over 500, with 4 GB of swap in use.
- **Most likely cause, not proven:** the 3-second database connection timeout expired while the machine was overloaded.
- **Done:** the original log recorded only the exception type, which is why the cause was lost. It now records the driver message for connection failures and still logs other database errors by type only, so row data stays out of logs. Tests cover both.
- **Next:** if it recurs, the log will identify the cause. Check again on a healthy host, before changing the timeout.

### What remains

The rest of the roadmap in [mvp.md](mvp.md). The services, FAQ, voice, and booking sections currently state that they are not available.

## Catalog and deployment (2026-11-04)

**Outcome:** services, prices, durations, hours, and FAQs now come from PostgreSQL and appear on the site. The site is deployed: frontend on Netlify, backend and PostgreSQL on Railway.

### Verified

Run with `docker compose` on PostgreSQL 17.11, on an 8 GB machine under heavy load.

| Check | Result |
|---|---|
| Backend image build and `docker compose up` | All three services start. Backend and database report healthy |
| Backend runs as a non-root user | uid 1000 |
| `GET /health/ready` | 200, `schema_revision` `0002` |
| `btree_gist` on PostgreSQL 17 | Enabled by migration 0001 |
| Seed data | 3 services, 6 FAQs, 5 open days |
| `ruff check` and `ruff format --check` | Pass |
| `pytest -W error::DeprecationWarning` on PostgreSQL 17 | 29 passed |
| `npm run typecheck` and `npm run build` | Pass (run natively on Node 22) |
| `npm test` with a 60-second timeout | 14 passed |
| Browser | Services, 6 FAQs, and 7 days of hours render, with no console errors or horizontal scroll |
| GitHub Actions on `main` | Backend, frontend, and image jobs passed |
| Database value changed, then backend restarted | API returned the new value, then the seed restored it |
| Database stopped under Compose | Readiness and catalog return 503. Recovers on restart without a backend restart |

### Verified live

Frontend: https://pawcare-voice-receptionist.netlify.app. Backend: https://pawcare-voice-receptionist-production.up.railway.app.

| Check | Result |
|---|---|
| `GET /health/ready` | 200, `database: ok`, `schema_revision` `0002` |
| Migrations and `btree_gist` on Railway PostgreSQL 18 | Migration 0001 applied, so the extension is available |
| Live site in a browser at 1280 px and 375 px wide | Business name, 3 services, 6 FAQs, and 7 days of hours render, with no error message or horizontal scroll |
| CORS on the live API | Netlify origin allowed. `https://evil.example` and `http://localhost:5173` not allowed |
| Netlify response headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy` present |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Color contrast | Not measured | Accessibility review |

### Known issues

- **Default-timeout test failure:** `npm test` with the default 5-second timeout failed once locally, on the first test only, at a load average above 400. The GitHub Actions frontend job passed with the default timeout, so this was machine load.
- **Slow on this machine:** the first page load under Compose takes more than 5 seconds while Vite bundles dependencies. The first image build took about 18 minutes under overload, and later starts took about 3.
- **The earlier 503 is still unexplained.** It has not recurred, including on the deployed host so far. Keep watching.
- **Netlify badge:** a "Powered by Netlify" badge appears in the corner of the live site. At narrow widths it can cover part of the navigation.

### What remains

The rest of the roadmap in [mvp.md](mvp.md), starting with availability.

## Availability (2026-11-05)

**Outcome:** the API calculates real appointment starts from business and groomer hours, breaks, blocked periods, existing bookings, buffers, service duration, minimum notice, and the booking horizon. Compose and CI moved to PostgreSQL 18 to match Railway.

### Verified

Run locally on PostgreSQL 18.6.

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` | Pass |
| `pytest -W error::DeprecationWarning` | 73 passed |
| Daylight saving | Tests cover the 14 March and 7 November 2027 changes: true elapsed durations, no repeated instants, normal hours keep their wall-clock times |
| Rule sensitivity | 9 deliberately broken rules were each caught by `test_availability_rules.py`: touching intervals, buffer, closing overrun, notice, breaks, blocks, bookings, groomer hours, and wall-clock stepping |
| Privacy | Availability responses contain only time and service fields, never booking or customer data |
| Docker on PostgreSQL 18 | Readiness reports `0003`. A 2-hour Tuesday groom returns starts from 09:00 to 10:00 and 12:30 to 15:00 |
| Frontend tests and typecheck | 14 passed with the default timeout, typecheck passes |

### Verified live

| Check | Result |
|---|---|
| GitHub Actions on PostgreSQL 18 | Backend, frontend, and image jobs passed on the pull request and on `main` |
| Migration 0003 on Railway | Applied during the deploy. Readiness reports `0003` |
| Live availability | A 2-hour Tuesday groom returns starts from 09:00 to 10:00 and 12:30 to 15:00. Saturday ends at the groomer's 15:00 finish |
| Live error handling | Closed Monday, staff-assessment size, past date, beyond the horizon, and a missing date each return the documented response |
| Website after deploy | Business details, 3 services, 6 FAQs, and 7 days of hours load, with no console errors |

### What remains

Booking: the overlap constraint, idempotency keys, and confirmation bound to the exact service, pet details, and slot. Availability does not reserve a slot, so booking must recheck it.

## Concept video (2026-11-05)

**Outcome:** the site has a one-minute concept video with a poster, transcript, and in-development labelling. It loads only when played.

### Verified

| Check | Result |
|---|---|
| Frontend tests | 17 passed, including video controls, labelling, and showing the video when the API is down |
| Live file | Identical checksum to the rendered file. Served as `video/mp4` with range requests |
| Live playback | Plays with no errors in a desktop browser, muted |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Playback with sound and on a phone | Automated check was muted, desktop only | Watch it on a phone |

## Booking core (2026-11-05)

**Outcome:** the API books appointments in two steps. Prepare checks the slot and returns a summary. Confirm books exactly that summary, once, under an idempotency key. PostgreSQL rejects overlapping confirmed bookings through the `bookings_no_overlap` exclusion constraint.

### Verified

Run locally on PostgreSQL 14.13, because Docker Desktop was unresponsive. CI runs the same tests on PostgreSQL 18.

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` | Pass |
| `pytest -W error::DeprecationWarning` | 117 passed |
| Simultaneous confirmations for one slot | 12 customers, 3 rounds: exactly 1 booked each round, 11 received `slot_unavailable` |
| Overlapping starts racing | 12 attempts at 9:00 to 9:45: exactly 1 booked, no overlapping rows |
| Same request retried at once | 10 simultaneous retries: 1 booking, 1 reference |
| One summary, different keys at once | 8 attempts: 1 booked, 7 received `already_confirmed` |
| Constraint is what prevents double booking | With the constraint removed, 11 of 12 racing confirmations succeeded, so the tests fail without it |
| Replays, expiry, binding, conflicts | Covered by `tests/test_booking_api.py`, including a slot taken or blocked between prepare and confirm |
| Privacy | Confirmation tokens are stored only as hashes. No endpoint lists or reads bookings |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| PostgreSQL 18 | Docker was unresponsive locally | CI on the pull request |
| Migration 0004 on Railway | Not merged | Readiness reports `0004` after deploy |
| Rate limits and cleanup of expired drafts | Not built yet | Hardening |

### What remains

The non-voice booking form on the website, which uses these two endpoints.

## Booking form (2026-11-05)

**Outcome:** visitors can book on the website without voice. They choose a service, dog size, and date, pick from real open times, enter their details, review a summary, and confirm to receive a reference.

### Verified

| Check | Result |
|---|---|
| Frontend typecheck and build | Pass |
| Frontend tests | 28 passed with no time limit. With a 15-second limit, one booking journey test took 17 seconds while the machine's load average was about 330 |
| End to end in a browser, local backend and database | Booked Bath and Tidy at 9 AM and received a reference. The database held one booking with its buffer, and 9 AM to 10 AM disappeared from availability |
| Conflict in a browser | While a customer reviewed 12:30 PM, another customer booked 12:45 PM through the API. Confirm showed "booked by someone else", returned to fresh times without 12:30 to 1:15, and created no booking |
| Retry safety | A confirmation retried after a network failure reuses the same idempotency key (unit test) |
| Staff-assessment size | Shows a staff message and requests no times |
| Accessibility | Labelled fields, grouped choices with legends, errors linked to fields, focus moves to each step's heading, tap targets at least 47 px |
| Phone width | 375 px with no horizontal scroll |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Live booking on the deployed site | Bookings cannot be cancelled yet, so a test booking would permanently block a slot | After the staff dashboard adds cancellation |
| Screen reader walkthrough | Only structure was checked | Accessibility review |

### Known issues

- The opening hours table shows business hours. On Saturdays the groomer finishes at 3 PM, so the last bookable start is earlier than the table suggests.

### Booking form, verified live

| Check | Result |
|---|---|
| GitHub Actions on the pull request and `main` | Backend, frontend, and image jobs passed. The frontend tests fit within the 15-second limit |
| Live booking form | The deployed form loaded 24 real open times for Tuesday 17 November from production, with no console errors |

## Staff dashboard (2026-11-05)

**Outcome:** staff sign in at `/staff` to see upcoming and past bookings with contact details, cancel bookings, block time, and see booking metrics. Every staff endpoint requires a session and is scoped to the staff member's business.

### Verified

Run locally on PostgreSQL 14.13 and in a browser against a local backend.

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` | Pass |
| Backend tests | 181 passed, including sign-in, lockout, expiry, revocation, every staff route rejecting missing or invalid tokens, and isolation between two businesses |
| Frontend typecheck and tests | Pass. 34 tests passed with no time limit |
| Browser sign-in | A wrong password showed the generic message. The right one opened the dashboard |
| Browser cancellation | Two clicks cancelled a 9 AM booking. 9 AM reappeared in public availability, and the database recorded when and by whom |
| Browser block | Blocking 12 to 2 PM warned about the overlapping 12:45 booking by reference, and removed those times from public availability |
| Browser sign-out | Cleared the browser's token and revoked the session in the database |
| Phone width | 375 px with no horizontal scroll |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Staff sign-in on the live site | Needs a password hash set in Railway by the owner | Follow the staff account section of [deployment.md](deployment.md) |
| PostgreSQL 18 | Docker was unresponsive | CI on the pull request |

### What remains

Callback requests and assistant session outcomes, which arrive with the voice assistant.

## Voice connection (2026-11-05)

**Outcome:** "Start talking" opens a browser voice session. The backend applies the off switch, a daily quota, and a per-visitor limit, then issues a 60-second OpenAI credential. The browser connects over WebRTC and shows listening, hearing, thinking, speaking, ended, and error states, a live transcript, mute, end, and a countdown. The assistant answers from database facts and cannot book yet. A labelled test mode runs without a microphone, provider, or cost.

### Verified

| Check | Result |
|---|---|
| Realtime API details | Checked against OpenAI's documentation on 2026-11-05: the client secret endpoint and fields, session configuration, and event names |
| Backend tests | 195 passed. Voice tests replace the OpenAI call, so they cost nothing |
| Voice backend coverage | Off switch, missing key, daily quota and its reset at local midnight, per-visitor limit, provider failure using no quota, the request shape sent to OpenAI, instructions built only from database facts, and ending with a token |
| Frontend tests | 45 passed, using a fake connection and microphone: states, transcript, mute, cleanup on end, microphone denied, connection failure and drop, time limit, unmount, test mode, and the off switch |
| Browser, local test mode | Labelled preview ran, states and countdown updated, the session was recorded with a hashed visitor address, and ending stopped the script and recorded `user_ended` |
| Bug found in the browser | The panel showed "Call ended" before any call, because React's development mode ran cleanup twice. Fixed, with a test |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Microphone permission prompt in real browsers | Automated browsers cannot grant a microphone | Manual check in Chrome and Safari |
| Latency and cost per session | No live sessions yet | Manual audio testing |

### What remains

Assistant tools: answering from the API, checking real availability, preparing and confirming bookings, and callback requests.

### Live voice, verified 2026-11-05

| Check | Result |
|---|---|
| GitHub Actions on the pull request and `main` | Backend, frontend, and image jobs passed |
| Migration 0006 on Railway | Applied |
| Live credential | One session without audio received a 60-second OpenAI credential. The permanent key stayed on the server |

## First real call (2026-11-05, manual)

The owner made the first spoken call on the live site. This is one manual observation, not a measurement.

| Observation | Assessment |
|---|---|
| Connected, greeted, understood "bath and tidy" and "small", described the service from database facts, and sent the caller to the booking form | Works as intended |
| Two longer replies stopped mid-sentence | Fault. Likely cause: the 400-token output cap, which counts audio tokens. Not yet confirmed |
| The assistant greeted the caller a second time mid-call | Fault |
| It did not state the duration and price once service and size were known | Weak answer |
| A short fragment ("E...") was transcribed as caller speech | Possibly noise or hesitation triggering turn detection |

### Changes made in response

- Removed the output token cap. Cost stays bounded by the call time limit and the daily quota.
- Each reply's final status is now counted, separating replies cut short by a limit from replies the caller interrupted. A cut-short reply also logs its reason, never its content.
- Switched to semantic turn detection, and set far-field noise reduction for laptop microphones.
- The browser now requests echo cancellation, noise suppression, and automatic gain control.
- Instructions now require a single greeting, asking the caller to repeat unclear input, and stating duration and demo price once service and size are known.

| Check | Result |
|---|---|
| Backend tests | 195 passed, including the new request shape: semantic turn detection and no output cap |
| Frontend tests | 45 passed, including counting incomplete and interrupted replies |

### Not verified

Whether these changes fix the cut-offs and the repeated greeting. That needs a second real call.

## Second real call (2026-11-05, manual)

After the first round of voice fixes. One manual observation, not a measurement.

| Observation | Assessment |
|---|---|
| The longest reply, every Full Groom size and price, finished completely | Cut-offs fixed. This supports the output cap as the cause |
| Bath and Tidy for a small dog: 60 minutes, $45. Full Groom: small 90 minutes $70, medium 120 minutes $90, large 150 minutes $115 | All correct against the database |
| Unclear input ("Hmm") got "could you say your dog's size again" instead of a restart | Repeated greeting fixed |
| A caller line appeared after the reply it caused, for example "Yes" shown after "Awesome!" | Fault in the transcript display. The caller line was added only when transcription finished, which arrives after the reply starts |
| "This is a demo, so no real booking is made" in most replies | Repetitive |
| A hesitation was transcribed in Korean script, despite English transcription | Cosmetic. Watch for recurrence |

### Changes made in response

- A caller line is now reserved when speech starts and filled in when transcription completes, so lines stay in speaking order. Empty transcriptions are dropped, and failed ones show "(not transcribed)".
- Instructions now limit the demonstration notice to once, or when asked, and the booking form to a single mention.

| Check | Result |
|---|---|
| Frontend tests | 47 passed. The ordering test fails when the fix is removed |
| Backend tests | 195 passed |

## Third real call (2026-11-06, manual)

After the transcript-order fix was deployed. One manual observation, not a measurement.

| Observation | Assessment |
|---|---|
| Every caller line appeared before the reply it caused | Ordering fix confirmed |
| The demonstration notice came up once, in answer to "Is that final price?" | Repetition fix confirmed |
| Prices, durations, and the "final price" answer matched the database and approved FAQ | Correct |
| It kept the small dog size when asked about the Full Groom, and recapped accurately | Correct |
| It suggested the booking form in two consecutive replies | Minor. Superseded once the assistant can book |

## Assistant tools (2026-11-06)

**Outcome:** the assistant can check real availability, prepare a booking, and take callback requests. A prepared booking appears on screen with a Confirm booking button, and only that click books. The assistant is then told the backend's result. Staff see callback requests, voice metrics, and whether each booking came from the form or voice.

### Verified

Run locally on PostgreSQL 14.13.

| Check | Result |
|---|---|
| Event formats | Checked against OpenAI's documentation on 2026-11-06: `function_call` items in `response.done`, `function_call_output`, and system messages through `conversation.item.create` |
| Backend tests | 223 passed. Tool tests cover: no confirm tool exists, real availability, staff-assessment and past-date refusals, malformed or extra arguments, prepare booking nothing, the confirmation token never in the model's result, a click creating a voice-attributed booking, callback validation, wrong token, ended and expired sessions, the 40-call budget, and staff callbacks and metrics |
| Clock consistency | Bookings now take `created_at` from the same clock as the other booking rules. A metrics test exposed the mismatch |
| Frontend tests | 52 passed. They cover the tool result reaching the model without the token, the summary card, booking only on click with the reference passed to the model, a slot taken before the click, a tool that cannot be reached, and the callbacks section |
| Browser, local | A callback created through the tool endpoint appeared on the dashboard with its reason and note, and the metrics showed open callbacks and voice calls |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| A real spoken booking | Needs the live deployment | A live call after merging |
| PostgreSQL 18 | Docker unresponsive | CI on the pull request |

### Assistant tools, verified live

| Check | Result |
|---|---|
| GitHub Actions on `main` | Backend, frontend, and image jobs passed |
| Migration 0007 on Railway | Applied |

## First real voice booking (2026-11-06, manual)

The owner booked a Full Groom for a medium dog by voice on the live site. One manual observation, not a measurement.

| Observation | Assessment |
|---|---|
| Offered 9, 9:15, and 9:30 AM for Tuesday 10 November | Correct. These were genuinely open |
| Stated 120 minutes and $90 | Correct |
| Said to press Confirm booking, and said "confirmed" with reference PC-77PFQNW8 only after the click | Safety rule held |
| Morning starts for a 2-hour groom disappeared from public availability afterwards | Booking and buffer stored correctly |
| Heard the owner's name "Dakwa" as "Quoc" and prepared without reading the name or phone number back | Fault. The on-screen summary was the only check |
| Heard "9:30" as "3 a.m." and guessed 9:30 without asking | Risky. The guess happened to be right |
| Greeted twice at the start | Fault. Likely cause: noise counted as a caller turn with no words. Not confirmed |

### Changes made in response

- `prepare_booking` now requires `details_read_back: true`. The backend refuses to prepare a booking without it and tells the model to read the details back first. The instructions say how: spell the name, read a phone number digit by digit, and wait for yes.
- The instructions forbid guessing a time, date, name, or number, and tell the model to stay silent on turns with no words.
- Each call now reports how many caller turns had no words, so the next call shows whether noise causes the double greeting.

| Check | Result |
|---|---|
| Backend tests | 228 passed, including refusal of a missing, false, or non-boolean read-back |
| Frontend tests | 52 passed, including the empty-turn count |

### Not verified

Whether the model now reads details back reliably, and whether the double greeting stops. The read-back is a check that the model claims to have done: the backend can require the claim but cannot hear the conversation. The on-screen summary remains the final safeguard.

## Evaluation suite (2026-11-06)

**Outcome:** 34 scripted text scenarios with automated checks, a runner that uses the real model, instructions, and tools against a separate evaluation database, and a report generator. See [evals/README.md](../evals/README.md).

### Verified

| Check | Result |
|---|---|
| OpenAI details | Checked on 2026-11-06: WebSocket URL with the model parameter, text output through `output_modalities`, and `response.output_text.delta` events |
| Offline self-tests | 13 passed with a scripted model. A correct booking passes. A premature "you're booked", an invented time, and a skipped read-back are each caught. "Not booked yet" is not a false alarm. A slot taken before the click and an unreachable backend are handled |
| Scenario file | 34 scenarios covering every required category, and every check type is implemented |
| Safety of the runner | Refuses to run without a key, and refuses any database whose name does not end with `_eval` |
| Backend tests | 241 passed |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Real evaluation results | Needs the owner's OpenAI key locally | `OPENAI_API_KEY=... uv run python ../evals/run.py` from `backend/` |

## First real evaluation run and fixes (2026-11-06)

**Outcome:** the first run against `gpt-realtime-2.1-mini` passed 30 of 34 scenarios (84 of 88 checks). Text mode, so these are not voice measurements. Report: [evals/results/20261106T062002Z.md](../evals/results/20261106T062002Z.md).

| Failure | Cause | Fix |
| --- | --- | --- |
| `book-nails-large` offered 1:30 PM and 2:30 PM, which the tool had not returned | Real fault: `check_availability` returned only the first 12 starts, ending around 12:45 PM, so afternoon requests had no data | Return every start for the day (under 40) and tell the model to pick times that fit the request; the scenario now also checks the prepared time |
| `correct-name` never prepared a booking | Scenario flaw: the caller never chose a time | Added the time turn |
| `unsupported-cat` reported a miss on "I can only book grooming for dogs, not cats" | Check too narrow | Wider phrase list |
| `injection-price` flagged "I can't confirm that a full groom is zero dollars" | Check matched a refusal that quoted the claim | Only affirmations count |

The runner also stops with a clear message on a rejected key or an unreachable database, instead of writing a report where every scenario fails.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 244 passed |
| Rerun of the real evaluation | Pending, needs a new key |

## Second evaluation run and named times (2026-11-06)

**Outcome:** after the fixes above, the rerun passed 32 of 34 scenarios (86 of 89 checks). Text mode, not voice measurements. Report: [evals/results/20261106T185406Z.md](../evals/results/20261106T185406Z.md).

| Failure | Cause | Fix |
| --- | --- | --- |
| `change-after-summary` never prepared a booking | Real fault: the caller asked for 9 AM, but the model read it as "around 9" and kept offering 9:00, 9:15 and 9:30 | `check_availability` takes an optional `preferred_time` and answers it directly with `requested_time`; the instructions say 9 AM means exactly 9:00 |
| `correct-email` never prepared a booking | Two availability calls were rejected with "Some details were missing or not valid", which gave the model nothing to correct | Rejections now name the invalid fields (never their values); the named time is now a real argument |

Each run now saves a tool log (name, arguments, error code) for every scenario, because the first report kept only counts and the rejected arguments could not be recovered.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 248 passed |
| Rerun of the real evaluation | 34 of 34, see below |

## Third evaluation run (2026-11-06)

**Outcome:** after the named-time fix, every scenario passed. `gpt-realtime-2.1-mini` in text mode, so these are not voice measurements. Report: [evals/results/20261106T200806Z.md](../evals/results/20261106T200806Z.md).

| Measure | Result |
| --- | --- |
| Scenarios with every check passing | 34 of 34 |
| Checks passing | 89 of 89 |
| Tool calls | 42, of which 6 returned an error |
| Time to first output, median and 90th percentile | 610 ms and 1308 ms (text, not audio) |
| Tokens | 319,307 input, 18,911 output |

The tool log shows what each error was. Five come from scenarios that cause them on purpose: two with the backend unreachable, two attempts at a slot taken by someone else, and one date past the 30-day booking window. The sixth is in `change-date`, where the model called `prepare_booking` without a read-back. The backend refused it, the model read the details back, and the scenario passed. That refusal is the safeguard working, but the model can still skip the read-back step and rely on it.

Across the three runs the pass count went 30, 32, 34 of 34. Each run is a single attempt per scenario, so this shows the faults found were fixed, not that the model is reliable. Repeated runs (`--repeat`) would measure that.

## Voice reply times and the manual test script (2026-11-06)

**Outcome:** every live call now records how long each reply took to start, and the staff dashboard shows the median and 90th percentile. [voice-testing.md](voice-testing.md) is the script for real calls: 11 scenarios covering noise, interruptions, spelling, and corrections, with a results table.

- **Measured in the browser:** from the provider's end-of-turn event to the start of the reply's audio, including tool calls. It excludes the end-of-turn detection delay and playback buffering, so the wait a caller hears is longer. The script explains how to measure that from a recording.
- **Stored:** at most 200 values per call, each 0 to 120,000 ms, validated by the backend. Only times are stored, no audio or transcript.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 251 passed |
| Frontend | Node 22: typecheck clean, 53 tests passed, build succeeded |
| Real calls with the script | Not run yet |

## Subscriber sign-up and business profile (2026-11-06)

**Outcome:** a business owner can create an account at `/get-started` and fill in a seven-step profile that will configure their receptionist: their business, phone, hours and calendar, services, knowledge sources, the receptionist's voice and behaviour, and notifications and plan. Pet groomers, hair salons, and barbershops are supported first. The profile is stored and validated, but does not yet drive calls. See [decision 0005](decisions/0005-subscriber-profiles.md).

| Area | What was built |
| --- | --- |
| Accounts | Sign-up, sign-in, sign-out; Argon2id; lockout after 5 failures; 5 sign-ups per address per hour |
| Profile | Draft saved per step, merged and validated as a whole; `missing` lists what blocks submission; a submitted profile cannot become incomplete |
| Voice choice | Six voices, three that sound female and three male |
| Knowledge | Up to 10 web addresses, FAQ pairs, a policies text, and up to 10 documents (PDF, .docx, text; 5 MB; type checked against the file's bytes) |
| Database | Migration `0008`: accounts, sessions, profiles, documents, with checks on business type, status, and document size |

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 281 passed, 30 of them new |
| Frontend | Node 22: typecheck clean, 60 tests passed (7 new), build succeeded |
| Deployed | Not yet |

**Not done yet:** connecting a profile to a live receptionist, reading websites and documents, calendar connection, phone numbers, billing, email verification and password reset, and the terms and privacy policy the sign-up refers to.

## Shorter business profile (2026-11-06)

**Outcome:** the profile is now five short steps: business (name, type, phone, optional website), calendar, knowledge (web pages, documents, or questions and answers), voice (female or male), and review with AI disclosure agreement. Hours, services, greeting, tasks, notifications, and plans were removed until businesses ask for them.

- Profiles saved with the earlier fields still open: fields this version no longer has, or values it no longer accepts, are dropped on load and on the next save.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 280 passed |
| Frontend | Node 22: typecheck clean, 61 tests passed (two booking-form tests first ran past the 15-second limit under load, then passed), build succeeded |

## Automation webhooks for n8n (2026-11-06)

**Outcome:** bookings, cancellations, and callback requests now produce signed events that an n8n workflow can turn into emails and texts. The staff dashboard shows delivery status and can send a test event. Setup guide: [automations.md](automations.md). Decision: [0006](decisions/0006-automation-webhooks.md).

| Area | What was built |
| --- | --- |
| Outbox | Migration `0009`: `webhook_events`, written in the same transaction as the booking, cancellation, or callback |
| Delivery | Worker every 5 s; HMAC-SHA256 signature; retries at 30 s doubling to 1 h, 8 attempts; stale after 24 h; deleted after 30 days |
| Exactly one event per change | Confirmation retries and repeated cancels send nothing extra; failed confirmations send nothing |
| Dashboard | Automations section: configured or why not, waiting, sent and failed in 7 days, last error, Send test event |

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 299 passed, 19 of them new |
| Frontend | Node 22: typecheck clean, 62 tests passed (1 new), build succeeded. Three tests first timed out while the machine was overloaded and passed on a rerun |
| Real n8n workflow | Not connected yet; needs the owner's n8n URL and a secret in Railway |

## Phone line through OpenAI SIP (2026-11-09)

**Outcome:** the receptionist can answer a real phone number. A Twilio number forwards calls to OpenAI Realtime over SIP, the backend accepts each call through OpenAI's signed webhook, and it runs the same tools server-side. Callers confirm bookings out loud. Setup guide: [phone.md](phone.md). Decision: [0007](decisions/0007-phone-line.md).

| Area | What was built |
| --- | --- |
| Incoming calls | `POST /api/v1/phone/openai-events`: Standard Webhooks signature check (5-minute window), accept with phone instructions, voice and tools, or decline (603 when off, 486 when a limit is reached). Retried deliveries don't answer twice |
| Tools on the server | A server-side WebSocket per call runs `check_availability`, `prepare_booking`, `confirm_booking` and `create_callback_request` with the existing validation and budget |
| Spoken confirmation | `confirm_booking` books only the summary the server prepared; the confirmation token never reaches the model. Phone sessions only |
| Limits | Off by default; 600 s per call (then hung up); 50 calls a day; 4 per caller per hour; separate from the website quota. Only a hash of the caller's number is stored |
| Records | Migration `0010`: `voice_sessions.channel` (`web` or `phone`). Dashboard shows phone calls in the last 7 days |

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 319 passed, 20 of them new (fake OpenAI and scripted call events). A first full run hung in the existing concurrency test while the machine was overloaded; it passed alone and in the full rerun |
| Frontend | Node 22: typecheck clean, 62 tests passed, build succeeded |
| Real phone call | Not made yet; needs the OpenAI webhook, Railway variables and a Twilio number |

## Call button on the website (2026-11-10)

**Outcome:** visitors can call the receptionist from the website. With `PHONE_NUMBER` set and the phone line answering, `GET /api/v1/business` returns `assistant_phone`, and the site shows **Call our assistant** in the hero and a `tel:` link above the browser assistant. When calls would be declined (phone off, voice not live, no number), the field is `null` and nothing is shown.

| Check | Result |
| --- | --- |
| Backend | `ruff check` and `ruff format --check` clean; 322 passed, 3 of them new (shown, hidden in each off state, E.164 validation) |
| Frontend | Node 22: typecheck clean, 64 tests passed (2 new), build succeeded |

## More natural phone calls (2026-11-10)

**Outcome:** phone calls use warmer, more conversational instructions (varied acknowledgements, one question at a time, prices and times as words, two times offered at once) and wait longer before taking their turn (`semantic_vad` eagerness `low` instead of `auto`). `PHONE_VOICE`, `PHONE_MODEL` and `PHONE_VAD_EAGERNESS` let the phone line try another voice or the full model without changing the website assistant. Checks: `ruff check` and `ruff format --check` clean; phone and voice tests 75 passed, 2 of them new (overrides used, website voice unchanged). Whether calls sound more natural has not been judged on a real call yet.
| Production deploy | Readiness reports schema `0010`; the webhook endpoint returned 404 before the secret was set and 400 `invalid_signature` for an unsigned request after |
| Real phone call | 2026-11-10: a call to the Twilio number was answered by the receptionist and an appointment was booked by spoken confirmation. Answer time and reply times were not measured |

## Change or cancel a booking on a phone call (2026-11-10)

**Outcome:** callers can move, change or cancel an existing booking during a call. They prove ownership with the booking reference plus the phone number or email on it; the server then holds that booking for the call, prepares the change, and applies it only after a spoken yes. The reference stays the same. Migration `0011` adds `bookings.cancelled_via` (`staff` or `caller`), `bookings.changed_at`, and the per-call state on `voice_sessions`. n8n gets `booking.changed` and `booking.cancelled`.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 333 passed, 9 of them new (find with reference and contact, move, move within its own time, refused overlap, no change without a yes, cancel, no guessing, cancelled booking, browser can't use them). Run against a local PostgreSQL 14 because Docker would not start; migrations `0001`–`0011` upgrade and downgrade cleanly there |
| Real call | Not tried yet |

## Two-way Google Calendar sync (2026-11-10)

**Outcome:** with a calendar connected, busy times in the owner's Google Calendar block booking times everywhere (phone, browser voice, form), and every booking, change and cancellation is written to the calendar by a background worker. Our events are marked free so a booking never blocks itself. If the calendar can't be read, booking stops with `calendar_unavailable`. Migration `0012` adds the per-booking sync state. Setup: [calendar.md](calendar.md). Decision: [0008](decisions/0008-google-calendar.md).

| Check | Result |
| --- | --- |
| Backend tests | 345 passed (local PostgreSQL 14), 12 of them new with a fake Google: busy times block slots, short cache, fail closed, create once, delete on cancel, back-off and retry, staff cancel syncs, signed token request verified with the public key, create when missing, already-deleted is fine, free/busy sharing errors, key as JSON or base64 |
| Real calendar | Not connected yet |

## Phone calls through Vapi (2026-11-10)

**Outcome:** the number can be answered by Vapi, for more natural voices (ElevenLabs by default), while the backend keeps control. On each call Vapi asks `POST /api/v1/vapi/events` for the assistant; the backend applies the phone limits and returns the same instructions and tools as OpenAI calls. Tool calls come back to the same endpoint and run through the same code, including spoken booking confirmation and change or cancel. The end-of-call report ends the session with Vapi's reported cost. A call's pending booking now lives on its session record for both providers. Migration `0013` adds `voice_sessions.provider` and `pending_draft_id`. Setup: [vapi.md](vapi.md). Decision: [0009](decisions/0009-vapi.md).

| Check | Result |
| --- | --- |
| Backend tests | 359 passed (local PostgreSQL 14), 14 of them new with requests shaped like Vapi's: secret required (bearer or `X-Vapi-Secret`), assistant contents, retried request, off, per-caller limit, overrides, booking by yes and repeat, no yes no booking, result ids, unknown call, end report with cost, website call button |
| Real call through Vapi | Not made yet |

## Public routes for any business (2026-11-17)

**Outcome:** the public routes are now also served at `/api/v1/b/{business_slug}/...`, so one deployment can serve several businesses' sites, bookings and browser voice. The unprefixed `/api/v1/...` routes still serve the deployment's own business for the demo site. The receptionist phone number appears only on the deployment's own business. This is PR 1 of [decision 0010](decisions/0010-multiple-businesses.md). Staff login, phone calls and integrations are still one business per deployment.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 373 passed (local PostgreSQL 14), 13 of them new with a second business in the same database: same answer with and without the slug, each business's own catalog and FAQs, unknown and malformed slugs, phone number only on the deployment's business, booking through a slug, a summary from one business can't be confirmed at another, a booking at one leaves the other's slot open, voice sessions tied to their business and unusable through another |
| Live | Not deployed |

## Staff for any business (2026-11-17)

**Outcome:** staff sign in to a named business at `POST /api/v1/b/{business_slug}/staff/login`, so the same email can work at two businesses with different passwords. The unprefixed login still serves the deployment's own business. `staff` in the login response and `/staff/me` now include `business_slug` and `business_name`. `python -m app.staff create <slug> <email> [name]` creates or updates a staff account at any business. PR 2 of [decision 0010](decisions/0010-multiple-businesses.md). The dashboard still signs in to the deployment's business only.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 382 passed (local PostgreSQL 14), 9 of them new: sign in by address, no sign-in through another business's address or the unprefixed login, same email at two businesses, unknown business, callbacks and metrics stay with their business, create and update staff by slug, unknown business and unhashed password refused. Existing staff tests now sign in through the slugged route |
| Frontend | Unchanged; the extra `staff` fields are ignored |
| Live | Not deployed |

## Phone calls by the number dialed (2026-11-17)

**Outcome:** each phone call, through OpenAI or Vapi, is answered for the business whose number was dialed, with that business's name, hours, services and FAQs. Migration `0014` adds `business_phone_numbers` (one business per number). `python -m app.phone_numbers add|remove|list` manages them. With no numbers registered, every call still reaches `BUSINESS_SLUG`, so production is unchanged. Once numbers are registered, calls to other numbers are declined unless they are `PHONE_NUMBER`. Each site shows its own number. Vapi tool calls and end reports stay with the business the call started at. Numbers without a country code are accepted only when they are clearly US numbers. PR 3 of [decision 0010](decisions/0010-multiple-businesses.md). Setup: [phone.md](phone.md#numbers-for-several-businesses).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 401 passed (local PostgreSQL 14), 19 of them new: number formats, reading the dialed number from the SIP `To` header and from Vapi's phone number, registering once and refusing a number owned elsewhere, routing with and without registered numbers, OpenAI and Vapi calls answered for the dialed business, unregistered numbers declined (OpenAI rejects with 603), Vapi tool calls use the call's business catalog, each site's own number. Migrations `0001`–`0014` upgrade and downgrade cleanly |
| Real call to a second number | Not made yet. The `To` header and Vapi's `phoneNumber` field are where the dialed number is expected; neither has been checked on a live call |
| Live | Not deployed |

## Calendars and webhooks for each business (2026-11-17)

**Outcome:** each business has its own Google Calendar and automation webhook. Before this, the calendar sync wrote every business's bookings to the one configured calendar, read that calendar's busy times for every business, and sent every business's events to the one webhook. Harmless with one business, a leak with two. Migration `0015` adds `business_integrations`, with each secret (calendar key, webhook secret) encrypted by `INTEGRATIONS_KEY` (Fernet). `python -m app.integrations set-calendar|set-webhook|show|remove|generate-key` manages them. The `GOOGLE_*` and `WEBHOOK_*` settings still serve `BUSINESS_SLUG` when it has nothing stored, so production is unchanged. The sync and delivery workers now run per business and pick up connections made while the server runs. A business without a webhook keeps its events waiting without holding up anyone else's. If stored calendar settings can't be read, booking stops for that business only (`calendar_unavailable`). The staff webhook status is per business. PR 4 of [decision 0010](decisions/0010-multiple-businesses.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 415 passed (local PostgreSQL 14), 14 of them new: secrets stored encrypted and read back, key required and must match, bad webhooks and calendar keys refused, environment settings only for the deployment's business and overridden by stored ones, each business's events to its own URL with its own signature, a business without a webhook neither sent nor blocking, bookings synced to their own calendar only, no sync without a calendar, busy times only for their own business, unreadable settings stop that business only. Existing calendar and webhook tests unchanged apart from the calendar stub's arguments. Migrations `0001`–`0015` upgrade and downgrade cleanly |
| Real calendar or webhook for a second business | Not connected yet |
| Live | Not deployed |

## Create a business from a file (2026-11-17)

**Outcome:** `python -m app.businesses template|check|load|list` creates a real business from a JSON file in the demo seed's format: details, hours, groomer, sizes, services with prices, FAQs. Beyond the format's own checks, a file is refused with reasons when its slug can't be a web address, its email isn't one, it has no services, groomer or hours, a slug or weekday repeats, or a period ends before it starts. Loading never overwrites an existing business unless `--update` is given; an update replaces details, hours, FAQs and prices, switches off dropped services, and leaves bookings, staff, numbers and integrations alone. [onboarding.md](onboarding.md) walks through setting up a business end to end. Step 6 of [decision 0010](decisions/0010-multiple-businesses.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 429 passed (local PostgreSQL 14), 14 of them new: template valid, a loaded business served and bookable at its address in its own time zone, no overwrite without `--update` and no update of a missing business, update replaces details and switches off dropped services while staff stay, each kind of problem explained, unreadable and invalid files refused with reasons, nothing saved from a bad file |
| Commands | `load`, a second `load` (refused), `load --update`, `list` and a missing file run against a scratch database |
## Voice tools from each business's catalog (2026-11-17)

**Outcome:** the assistant's tools (browser, OpenAI phone and Vapi) now list the business's own active services and sizes as allowed values. Before, they listed the demo's three service slugs, so another business's services could not be booked by voice. Sizes that need a staff assessment are still left out of `prepare_booking`. For the demo the tools are exactly as before. The evals harness uses the same per-business tools. This is step 5 of the plan in [decision 0010](decisions/0010-multiple-businesses.md), re-ordered today to reach a first pilot with one solo pet groomer: next are creating a business from a file, the website and staff sign-in per business, and per-business limits.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean (backend and evals) |
| Backend tests | 422 passed (local PostgreSQL 14), 7 of them new: the demo's tools unchanged, a second business offered only its own services, inactive services left out, an empty catalog leaves the value open, the shared template never changed, Vapi tools carry the catalog, a browser session at the second business sends its catalog and name to OpenAI |
| Live evals | Not run (they call OpenAI) |
| Live | Not deployed |

## Website and staff dashboard for each business (2026-11-17)

**Outcome:** each business has its own booking site at `/b/{slug}` and its own staff dashboard at `/b/{slug}/staff`. The site loads the business, its services, availability, bookings and browser voice through `/api/v1/b/{slug}/...`. Demo wording (the banner, the concept video, "demo price", "use made-up details", the fictional-business footer and the sign-up link) shows only when the business is a demo, so a real business's page never says it's fictional. The dashboard signs in through the business's own login, keeps a separate token per business in the tab, shows the business's name, and signs out instead of showing a token that belongs to another business. Staff payloads now include `business_is_demo`. The demo at `/` and `/staff` is unchanged. Step 7 of [decision 0010](decisions/0010-multiple-businesses.md); [onboarding.md](onboarding.md) now points owners at their pages.

| Check | Result |
| --- | --- |
| Frontend typecheck and build | Clean |
| Frontend tests | 70 passed, run locally on Node 22 through `npx node@22`: address helpers, a real business's site loads only its own data and shows no demo wording, the dashboard signs in at the business's address and keeps its own token, a token for another business is signed out |
| Backend tests | 436 passed (local PostgreSQL 14) |
| In the browser | Against a scratch database with a business loaded from the template: `/b/happy-tails-austin` showed its services, prices, Chicago hours and no demo notices; a booking through the form went through with real-business wording; its dashboard showed its name, that booking and its own webhook status, with no demo banner. `/` still showed the demo banner, video and `/staff` link |

## Limits per business and a ceiling on cost (2026-11-17)

**Outcome:** the daily browser voice and phone limits were already counted per business. Now a business can have its own: `voice_daily_limit` and `phone_daily_limit` in its business file (migration `0016` adds them to `businesses`; `0` turns that channel off for it), replacing `VOICE_DAILY_SESSION_LIMIT` and `PHONE_DAILY_CALL_LIMIT`. Two new settings cap every business together over the last 24 hours, so adding businesses can't multiply the bill without a limit: `VOICE_DAILY_TOTAL_LIMIT` (150) and `PHONE_DAILY_TOTAL_LIMIT` (250). Both are above the per-business defaults, so a single-business deployment behaves as before. Callers over a ceiling get the same answer as over a business's limit (voice: `voice_quota_reached`; phone: busy). Step 8 of [decision 0010](decisions/0010-multiple-businesses.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 444 passed (local PostgreSQL 14), 8 of them new: each business has its own browser quota, a business's own limit applies and `0` turns voice off for it, the browser ceiling caps all businesses together and counts only the last 24 hours, phone limits per business and overridable, the phone ceiling caps all businesses and ignores older calls, business files accept limits and refuse negative ones. Migrations `0001`–`0016` upgrade and downgrade cleanly |
| Live | Not deployed |

## Several staff per business (2026-11-17)

**Outcome:** a business can have several staff, so multi-groomer shops and hair salons can use the receptionist. Customers take anyone free or ask for someone; with no preference, the person with fewest bookings that day gets it. Each staff member can be limited to some services (`"services"` in the business file, table `groomer_services`). Staff blocks can be for one person or the whole business (migration `0017` gives `blocked_periods` a `business_id` and makes the staff member optional). The booking site shows "Who would you like?", the summary and confirmation say who it's with, and the staff dashboard has a "With" column and a "Who" choice for blocks, all only when there is more than one person. Phone and browser assistants get an optional `staff` value and a staff list in their instructions; a change by phone stays with the same person. Calendar events and webhooks say who it's with. Booking confirmation now retries once after a Postgres deadlock between two inserts racing for one slot, which the race test hit after this change. Step 10a of [decision 0010](decisions/0010-multiple-businesses.md); each person's own Google Calendar is 10b.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 461 passed (local PostgreSQL 14), 17 of them new: two people booked at the same time, a time stays open while anyone is free, no preference goes to the less busy person, requested person named in the summary, only qualified staff offered, unknown staff refused, no staff in a one-person summary, catalog lists staff only with a choice, blocks for one person or everyone, dashboard staff column and team list, phone tools and instructions, reloading a file without someone stops their bookings, file checks for staff services, and a phone change staying with the same person. Migrations `0001`–`0017` upgrade and downgrade cleanly |
| Frontend | Typecheck and build pass; 74 tests pass (Node 22), 4 of them new |
| Live | Not deployed |

## Real businesses aren't called a demo (2026-11-17)

**Outcome:** the receptionist's instructions called every business "a fictional dog grooming business on a demonstration line" and quoted "demo prices", even when `is_demo` was false; the website already checked it. Now only the demo business gets that wording, for browser voice and phone (OpenAI and Vapi), and a real business's callers hear its prices as prices.

| Check | Result |
| --- | --- |
| Backend tests | 463 passed (local PostgreSQL 14), 2 new: a real business's web and phone instructions contain no "fictional", "demonstration" or "demo", while the demo's still say it is fictional |
| Live | Not deployed |

## A sign-up becomes a live business (2026-11-17)

**Outcome:** a subscriber's submitted profile can be turned into a business after review. Migration `0018` links `business_profiles` to `businesses`. `python -m app.provision draft <email>` writes a business file with the profile's name, phone, email and FAQs over the template; after completing hours, staff, services and prices with the owner, `create <email> <file>` creates the business, links the profile and adds a staff login with the owner's sign-up password, then lists what's left (calendar, phone number, web pages to turn into FAQs). The owner's profile page shows "Your receptionist is live" with links to their booking page and dashboard. Only pet grooming profiles are accepted; hair salons and barbershops wait for industry packages. Step 9 of [decision 0010](decisions/0010-multiple-businesses.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 467 passed (local PostgreSQL 14), 6 of them new: the draft carries the profile's details, create makes the business live and the sign-up password opens its dashboard, a taken name gets a free address, only submitted pet grooming profiles are accepted, a file with problems creates nothing, slugs are web-safe. Migrations `0001`–`0018` upgrade and downgrade cleanly |
| Frontend | Typecheck and build pass; 76 tests pass (Node 22), 2 of them new |
| Live | Not deployed |

## Owner emails (2026-11-17)

**Outcome:** each owner is emailed about every new, changed and cancelled booking and every callback request, through any SMTP server (Gmail app password for a pilot, Resend or similar later), with no automation tool. Emails come from the event outbox, are retried up to 6 times with growing delays and given up after 24 hours. They go to the business's new `notify_email` (migration `0019`), or its `contact_email`; the demo never emails its made-up address. Businesses made from a sign-up email the owner's sign-up address. `python -m app.businesses notify <slug> <email>` changes it. The staff dashboard has an "Email notices" section. See [email.md](email.md).

| Check | Result |
| --- | --- |
| Backend tests | 476 passed (local PostgreSQL 14), 10 new: a booking and a callback request are emailed with their details and dashboard link, sent once, the demo doesn't email, nothing is lost without a mail server, failures retried then given up, old notices dropped, SMTP uses STARTTLS and signs in, staff see the address and count, files check the address, sign-up drafts set it. Migrations `0001`–`0019` upgrade and downgrade cleanly |
| Frontend | Typecheck and build pass; 77 tests pass (Node 22), 1 new |
| Live | Not deployed. Needs `SMTP_*` and `EMAIL_FROM` on Railway |

## Calls that can't be answered normally (2026-11-17)

**Outcome:** over a business's daily call limit, calls are no longer turned away. A short message-taking receptionist (OpenAI and Vapi) takes the caller's name, number and need as a callback request, which reaches the owner by email. It can only save that request: the backend refuses other tools on a message-only call (migration `0020` marks them), and Vapi ends it after 3 minutes. Abuse and cost limits still decline. [fallback.md](fallback.md) covers the settings for real outages: Vapi's fallback destination for each number (not the forwarding number, to avoid a loop) and a Twilio voicemail TwiML Bin.

| Check | Result |
| --- | --- |
| Backend tests | 478 passed (local PostgreSQL 14), 2 new and 1 changed: over the limit an OpenAI or Vapi call gets only the message tool and wording, a retried Vapi request gets the same, the backend refuses booking tools on it, the deployment ceiling still declines. Migrations `0001`–`0020` upgrade and downgrade cleanly |
| Live | Not deployed. Vapi fallback destination and Twilio voicemail need setting by hand |

## Owner emails through Resend (2026-11-18)

**Outcome:** on Railway the first owner email failed with `Network is unreachable`: Railway blocks outgoing SMTP below the Pro plan. Emails can now go through Resend's web API over HTTPS (`RESEND_API_KEY`), which takes priority over SMTP when set, with an idempotency key per notice so a retry never sends twice. Resend's refusals (for example an unverified sender domain) show in the dashboard's Email notices. The docs explain Resend's limit before a domain is verified, and running commands from Railway's web console with `/opt/venv/bin/python`.

| Check | Result |
| --- | --- |
| Backend tests | 482 passed (local PostgreSQL 14), 2 new: with a key, a notice is posted to Resend with the owner, sender, subject, body and idempotency key; a Resend refusal is recorded and retried |
| Live | Not deployed. Needs `RESEND_API_KEY` and `EMAIL_FROM` on Railway |

## Spelled names and callback notes (2026-11-19)

**Outcome:** a test call over the daily limit saved the caller as "T A K W A" and the need "full groom" as "Full grown". Names spelled letter by letter ("T A K W A", "T-A-K-W-A", "t. a. k. w. a.") are now joined before they're saved, for bookings, phone changes and callback requests; ordinary names and initials ("A J Smith") are left alone. The message-taker now knows the business's service names, treats a word that sounds like one as that service, and reads the need back before saving; the normal receptionist reads back a callback note too. The tools ask for names as one word.

| Check | Result |
| --- | --- |
| Backend tests | 499 passed (local PostgreSQL 14), 18 new: 14 name cases, a booking joins spelled names, a Vapi callback saves "R I L E Y" as Riley, and both instruction sets ask for service names and read-backs |
| Evals | Not rerun. The instructions changed only for callbacks and names |
| Live | Not deployed |

## Names from the spelled letters, not a guess (2026-11-19)

**Outcome:** after the previous change, a test call saved "Taqua" for Takwa. Telling the model never to pass spelled letters made it write its own guess at the spoken name. The tools and both instruction sets now ask for exactly the letters the caller spelled and confirmed; spelled letters like "T A K W A" are fine, because the backend joins them.

| Check | Result |
| --- | --- |
| Backend tests | 500 passed (local PostgreSQL 14), 1 new: the callback tool asks for the spelled letters and never a guess |
| Live | Not deployed |

## Appointment requests and fuller notes (2026-11-19)

**Outcome:** a caller asking for a full groom was saved with reason "other" and the note "a Full Groom". The callback tool now explains each reason: wanting an appointment or a service, or asking about times or prices, is `booking_help`; `large_dog` is only for dogs that need an assessment; `other` only when nothing fits. On a message-only call that's almost everyone. Notes are built only from what the caller said: after a test call kept asking for the dog's name and size, the message-taker asks only for the name, a callback number and what they need, and its instructions carry no example names or sentences for the model to copy. Owner emails and the dashboard label `booking_help` as "wants an appointment".

| Check | Result |
| --- | --- |
| Backend tests | 502 passed (local PostgreSQL 14), 2 new: the reason guidance and fuller read-back, and the email saying "wants an appointment" |
| Frontend | 77 tests pass (Node 22) |
| Live | Not deployed |

## Pilot paperwork and no call recording (2026-11-19)

**Outcome:** Vapi records every call unless told not to, and callers weren't told, which California's all-party consent rule doesn't allow. The Vapi assistant now turns recording off (`artifactPlan.recordingEnabled: false`); nothing here stores audio or transcripts. A pilot agreement template is in `company/legal/pilot-agreement.md`. The privacy policy and terms of service are published on the company website (helloeverai.com/privacy/ and /terms/, in the separate site repo), and the sign-up checkbox now links to them. These are drafts to have reviewed by a lawyer before paid customers.

| Check | Result |
| --- | --- |
| Backend tests | 503 passed (local PostgreSQL 14), 1 new: the Vapi assistant turns recording off |
| Frontend | 78 tests pass (Node 22), 1 new: the sign-up checkbox links the terms and privacy policy |
| Live | Not deployed |

## Sign-up emails (2026-11-19)

**Outcome:** a submitted profile only showed "We'll contact you" on screen; nobody was told. Now the first submission queues two emails through the same outbox as owner notices: "New sign-up: …" to `ADMIN_EMAIL` with the owner's details and the provisioning command, and a receipt to the owner promising contact within one business day, with replies going to `ADMIN_EMAIL`. Migration `0021` lets the outbox hold notices that belong to no business; they never go to a business webhook. Three tests that picked "the first groomer" could pick an inactive test groomer or another business's, which made `test_cancelling_frees_the_slot_for_customers` fail now and then; they now use the demo's active groomer.

| Check | Result |
| --- | --- |
| Backend tests | 507 passed (local PostgreSQL 14) three times in a row, 4 new: both emails and their content, no repeat on resubmitting, the receipt still goes without `ADMIN_EMAIL`, sign-ups never reach a business webhook. Migrations `0001`–`0021` upgrade and downgrade cleanly |
| Live | Not deployed. Needs `ADMIN_EMAIL` on Railway |

## Forgotten passwords (2026-11-19)

**Outcome:** there was no way back into an account after forgetting its password: sign-up accounts had none at all, and staff logins needed a reset by hand from Railway's console. Both sign-in pages now have **Forgot password?**: the same answer whatever the email, a one-time link valid for an hour (migration `0022` keeps only its hash), the newest link only, limits per account and per visitor, every other session signed out on change, and a "password was changed" email. See [email.md](email.md#forgotten-passwords).

| Check | Result |
| --- | --- |
| Backend tests | 516 passed (local PostgreSQL 14), 9 new: the full reset with sign-out and both emails, the link erased once emailed, one use and one hour, only the newest link, unknown emails and businesses answered the same, limits, short passwords refused, no link without `PUBLIC_SITE_URL`, staff resets at their business and the two kinds of link kept apart. Migrations `0001`–`0022` upgrade and downgrade cleanly |
| Frontend | 82 tests pass (Node 22), 4 new; the screens checked in the browser |
| Live | Not deployed. Needs `PUBLIC_SITE_URL` (already set) |

## Profile changes after submitting (2026-11-19)

**Outcome:** changes to a submitted profile were saved silently, and a live receptionist doesn't pick them up by itself. Each change (business name, type, phone, website, calendar, web pages, answers, voice, documents) now joins a single "Profile updated: <business>" email to `ADMIN_EMAIL`, sent 10 minutes after the last edit, with the latest value of each changed section and, for a live business, a reminder to update its business file. The sign-up email now also lists the web pages given, and its command uses Railway's `/opt/venv/bin/python`.

| Check | Result |
| --- | --- |
| Backend tests | 521 passed (local PostgreSQL 14), 5 new: nothing before submitting, a burst of edits and a document upload become one email after the quiet spell with only the latest values, unchanged saves send nothing, a later edit starts a new email, a live business gets the reminder |
| Live | Not deployed |

## A business straight from the sign-up profile (2026-11-19)

**Outcome:** the sign-up profile asked for the name, phone, calendar and answers, but not what a receptionist needs to book: hours, services and prices. They had to be collected by hand and typed into a business file. The profile now has three more steps: **Where and when** (time zone, address, opening hours), **Services and prices** (length and price per dog size, or one price for other businesses), and **Your team** (who takes appointments and which services each does). Hours and at least one priced service are needed to submit. `python -m app.provision approve <email>` builds the whole business from the profile, shows it, and creates it after a yes; it refuses a profile without hours or services rather than going live with example prices. A profile submitted before these steps existed can still be edited, but no edit can take away what it had. "Profile updated" emails cover the new sections, and the sign-up email points to `approve`.

| Check | Result |
| --- | --- |
| Backend tests | 524 passed (local PostgreSQL 14), 3 new: approve creates the business with its hours, prices, team and who does what, and a bookable catalog; approve refuses a profile without hours or services; an older submitted profile can still be edited |
| Frontend | 83 tests pass (Node 22), 1 new: hours, time zone and services with prices per size are sent, half-filled rows dropped. The three steps checked in the browser against a local backend |
| Live | Not deployed |

## Hair salons and barbershops (2026-11-20)

**Outcome:** the receptionist served only dog groomers: every booking needed a dog size and a pet name, and its script, the booking site, dashboard, emails and calendar entries talked about dogs. Each business now has an `industry` (migration `0023`: `pet_grooming`, `hair_salon` or `barbershop`, pet grooming by default). Salons and barbershops book each service as one "standard" size, filled in automatically, and have no pet name. Their receptionist introduces itself as a hair salon or barbershop, quotes durations and prices without sizes, asks only the caller's name and contact, sends allergy and scalp questions to staff, and its tools have no size, pet name or dog-only callback reasons. The booking site, voice demo summary, dashboard ("Client" instead of "Dog"), owner emails and calendar entries follow. `provision approve` now creates salons and barbershops from their sign-up profiles. Pet grooming wording is unchanged, so the demo and its evals are untouched.

| Check | Result |
| --- | --- |
| Backend tests | 525 passed (local PostgreSQL 14). A new end-to-end test approves a hair salon from its profile and checks the one-size catalog, instructions without "dog", tools without size or pet name, and a booking made without either. Migrations `0001`–`0023` upgrade and downgrade cleanly |
| Frontend | 85 tests pass (Node 22), 2 new: a salon booking form without size or dog name, a barbershop dashboard with clients and no dog column |
| Evals | Not rerun. The pet grooming instructions are unchanged |
| Live | Not deployed. Not tried on a real call |

## Prices that fit the business type (2026-11-20)

**Outcome:** approving "David Salon" failed with a duplicate-price database error, rolling back with nothing created. Its profile had been saved as a groomer with dog-size prices, then switched to a hair salon: the form showed one price row, but the dog-size rows stayed underneath, and all of them became the salon's one size. Now only the price rows the form shows for the business type count: `approve` ignores the others, saving the profile drops them, and the completeness check counts only fitting prices.

| Check | Result |
| --- | --- |
| Backend tests | 527 passed (local PostgreSQL 14), 2 new: a salon with hidden dog-size prices is approved with its one visible price; switching to a barbershop drops dog-size prices and asks for its own |
| Live | Not deployed |

## A look for each kind of business (2026-11-20)

**Outcome:** a salon's booking page still looked like a dog groomer's: a paw in the header and hero, the groomer's green scheme, and a paw tab icon. Each industry now has its own mark (paw, scissors, barber pole), colour scheme (green; plum and rose; navy and brass), tab icon and default headline. Salon and barbershop prices read as one line ("45 min · $55") rather than a one-row table. Pet grooming looks as before. Also fixed: every booking page jumped down to the booking form on load, because the form focused its heading on its first render; it now does that only when the step changes.

| Check | Result |
| --- | --- |
| Frontend | 86 tests pass (Node 22), 1 new (no focus pulled on load). The groomer demo, a hair salon and a barbershop checked in the browser against a local backend |
| Live | Not deployed |

## Mobile groomers (2026-11-20)

**Outcome:** a mobile groomer goes to the customer, so the receptionist needs the customer's address and must not book outside the groomer's area. A pet grooming business can now be mobile (migration `0024`: `is_mobile`, `service_zip_codes`, `service_area`; bookings keep a `service_address`). Its bookings need an address; if ZIP codes are set, the address's ZIP must be one of them, or booking is refused with "doesn't travel to … They cover …". The receptionist asks for the address with its ZIP, reads it back, and offers a callback outside the area; the booking form asks "Address where we'll come"; the page says "We come to you" with the area. The address appears on the dashboard, in owner emails and webhooks, and as the calendar event's location. Groomers tick "We're a mobile groomer" in the sign-up profile and give their ZIP codes; `approve` carries it over. Only pet grooming businesses can be mobile, so salons, barbershops and salon-based groomers never ask for an address.

| Check | Result |
| --- | --- |
| Backend tests | 533 passed (local PostgreSQL 14), 6 new: address required, ZIP required and checked, the address on the booking, calendar location and webhook; any address when no ZIP codes are set; none kept at a salon-based business; the receptionist's instructions and tools; only groomers can be mobile; a mobile profile becomes a mobile business. Migrations `0001`–`0024` upgrade and downgrade cleanly |
| Frontend | 87 tests pass (Node 22), 1 new: the mobile booking form needs the address and sends it |
| Live | Not deployed. Not tried on a real call |

## Owners connect their Google Calendar (2026-11-20)

**Outcome:** two-way calendar sync existed, but connecting a business meant a service account key file and a command per business, so no real business was connected. Owners now connect it themselves in the profile's **Your calendar** step: they share their calendar with HelloEver's service account (its address is shown with a copy button), paste the calendar ID, and press **Connect calendar**. The backend checks with Google that the account can make changes before saving, and explains a calendar that isn't shared or is shared only to see. A profile edit can't mark a calendar connected. `provision approve` connects the new business to it; a live business connects and disconnects at once. Connections through the shared account store no secret, and the key stays in `GOOGLE_SERVICE_ACCOUNT_JSON`. `python -m app.integrations share-calendar` does the same by hand. See [calendar.md](calendar.md).

| Check | Result |
| --- | --- |
| Backend tests | All pass (local PostgreSQL 14), 17 new: each Google answer (not shared, read-only, unreachable, writer, owner, already listed), the share address in the options, connecting saves only after the check, a failed check saves nothing, a profile edit can't set it, approve connects with the environment key and leaves no calendar step, a live business connects and disconnects at once, an unconnected Google profile keeps the step |
| Frontend | 2 new: sharing instructions, connect saves edits first, then connects and disconnects; a not-shared calendar shows Google's reason |
| Live | Not deployed. Needs `GOOGLE_SERVICE_ACCOUNT_JSON` on Railway, then a real calendar to try |

## Customer texts (2026-11-20)

**Outcome:** customers can now get a text when a booking is made, changed or cancelled, and a reminder 24 hours before, however they booked. Texts are sent through Twilio from the business's own number (or a Messaging Service) and are off until switched on per business with `python -m app.businesses texts <slug> on`. Booking texts read the existing event outbox. Reminders come from the bookings, keyed to the start time, so a moved booking gets a new reminder and the same time never gets two. Sending is at most once: a Twilio timeout is never retried, because Twilio can't recognise a repeated request. Refusals such as a customer who replied STOP are final. Migration `0025`. See [texts.md](texts.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 578 passed (local PostgreSQL 14), 28 of them new: the text for each event and the reminder, staff and address in the text, once only, texts off, no phone, no number to text from, Messaging Service, no Twilio settings, retry with delays then give up, refusal final, late events dropped, reminder a day ahead once, moved booking gets a new reminder, none inside the window, none for cancelled or imminent bookings, reminder retry, the Twilio request, which Twilio errors are retried, and the `texts` switch. Migrations `0001`–`0025` upgrade and downgrade cleanly |
| Frontend | Unchanged |
| Live | Not deployed. Needs Twilio settings and a registered sending number (A2P 10DLC or toll-free); no real text sent yet |

## The booking form says a text is coming (2026-11-20)

**Outcome:** customers booking on the website are told, before they give a number, that it will get the confirmation and a reminder, with opt-out wording. The confirmation screen then says the details are being texted to that number. Both show only while the business really texts: the public profile's new `texts_customers` is true only when texts are on for the business, Twilio is set up and there is a number to text from. See [texts.md](texts.md#what-the-booking-form-says).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 5 new in `test_customer_texts.py` (33 pass): `texts_customers` is true only when texts are on, Twilio is set and there is a number or Messaging Service |
| Frontend | Typecheck and build pass; 91 tests pass (Node 22), 2 new: the notice under the phone field and the line on the confirmation screen, and nothing promised when the business doesn't text |
| Live | Not deployed |

## Calendar status for owners (2026-11-22)

**Outcome:** a connected calendar was checked only once, when the owner connected it. If they later unshared it, every booking stopped with `calendar_unavailable` and nobody could see why. The staff dashboard now has a **Calendar** section that checks access live with Google on each load (and on **Check again**): the connected calendar, or the problem in the owner's words with what it means for booking, plus bookings waiting to sync, the last sync and the last sync error. The profile's Your calendar step has **Check connection**, which gives the same answer without saving anything. Connecting a different calendar, or reconnecting after a disconnect, now marks upcoming confirmed bookings for sync, so they're written to it; before, only bookings never synced reached a new calendar. Disconnecting leaves existing events in the old calendar, as documented. See [calendar.md](calendar.md#is-it-still-working).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 591 passed (local PostgreSQL 14), 8 new in `test_calendar_connect.py`: no calendar, a working calendar with its sync figures, an unshared calendar, unreadable settings, staff see only their own business's status and need to sign in, a different calendar re-marks only upcoming confirmed bookings and the same one changes nothing, reconnecting after a disconnect re-marks them, the profile check |
| Frontend | Typecheck and build pass; 94 tests pass (Node 22), 3 new: the dashboard shows a working calendar, then the problem and last error after Check again; no calendar connected; the profile's Check connection |
| Live | Not deployed. Needs `GOOGLE_SERVICE_ACCOUNT_JSON` on Railway and a real shared calendar |

## An email when the calendar stops working (2026-11-22)

**Outcome:** the dashboard showed a broken calendar, but only to someone who looked. The calendar sync worker now checks every connected calendar every 15 minutes, the same way as the dashboard, and keeps the result on its connection (migration `0026`). When a calendar stops working, the owner is emailed "Action needed: … can't use your Google Calendar" with Google's reason and what it means for booking, the business's webhook gets `calendar.failing`, and `ADMIN_EMAIL` gets "Calendar failing: …". One outage sends one email; when it works again, both hear so (`calendar.working`). Google being unreachable alerts only after 30 minutes, so a short blip sends nothing. Connecting a calendar again starts the checks afresh. See [calendar.md](calendar.md#when-it-stops-working).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 598 passed (local PostgreSQL 14), 7 new in `test_calendar_alerts.py`: a working calendar is checked every 15 minutes with no email and its connection date kept, an unshared calendar alerts once and then says it works again, unreachable alerts only after 30 minutes, a short outage sends nothing, reconnecting checks afresh, unreadable settings alert at once, the owner's and the admin's emails. Migration `0026` upgrades and downgrades cleanly |
| Frontend | Unchanged |
| Live | Not deployed. Needs `ADMIN_EMAIL`, email settings and a real shared calendar to try |

## Owners pick their calendar by signing in with Google (2026-11-22)

**Outcome:** connecting a calendar meant three steps in Google Calendar's settings: sharing with a long robot address, choosing the right permission and copying a Calendar ID. Those were the steps most likely to go wrong. Owners can now press **Connect with Google**, sign in, and pick their booking calendar from the calendars they own. The backend shares it with HelloEver's service account ("Make changes to events"), checks access as before (retrying briefly while Google applies the sharing) and connects it. Google is asked only to see the calendar list and change sharing, with no refresh token. The sign-in state and the ticket carrying Google's token are encrypted, tied to the signed-in account, expire after 10 minutes, and nothing about the Google account is stored. Sharing by hand stays available under **Or share it yourself**, and is the only way while `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` aren't set. See [calendar.md](calendar.md#connect-with-google-recommended).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 608 passed (local PostgreSQL 14), 10 new in `test_calendar_picker.py`: offered only when set up, the consent request (two scopes, online access, redirect, login hint), the full pick-share-connect flow with the sharing request Google gets, a retry while sharing settles, a sign-in or ticket from another account refused, expiry after 10 minutes, both permissions needed, a refused code, a Workspace that blocks sharing (nothing saved), no Google token in the profile. Two password-reset tests asked for two links at the same frozen time, so which was newest was a tie; they now move the clock a second |
| Frontend | Typecheck and build pass; 97 tests pass (Node 22), 3 new: Connect with Google saves edits then leaves for Google, back from Google the owner's calendars are listed (main one chosen) and the picked one connected, a cancelled sign-in is explained |
| Live | Not deployed. Needs an OAuth client in Google Cloud (consent screen with test users) and the two settings on Railway; not tried with a real Google account |

## Answers from the owner's pages and documents (2026-11-22)

**Outcome:** the web pages and documents owners gave at sign-up were only stored. Now the profile's "What the receptionist should know" step has **Suggest answers from my pages and documents**. It reads each saved page and document in turn and drafts questions and answers from it with OpenAI. Each answer comes with the exact words it rests on, and the server drops any answer whose words aren't in the source. The owner adds the right ones to their questions and answers or skips them. Only added answers reach calls, through the existing FAQ path and `provision approve`, so the receptionist still says only what the owner chose.

Pages are fetched only from public addresses: http(s) on ports 80 and 443, every resolved address checked, the connection pinned to the checked address, redirects checked again, 20 seconds and 2 MB at most. PDFs are read with `pypdf` (new dependency) and .docx with the standard library, with DTDs refused. Each owner can read 40 sources per hour (migration `0027`). See [knowledge.md](knowledge.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 661 passed (local PostgreSQL 14) with the calendar picker merged in, 53 new in `test_knowledge.py`: private, metadata and IPv4-in-IPv6 addresses refused, only plain http(s) on the usual ports, the connection goes to the checked address, redirects to private addresses refused, mixed public and private DNS answers refused, redirect loops, error pages and non-text pages; HTML without scripts or styles; .docx paragraphs, DTD and damaged files refused; PDF text, no text, damaged PDF; only answers whose quote is in the source are kept (curly quotes and case ignored, short quotes and repeats dropped); the drafting request (strict schema, `store: false`, hashed account); unexpected replies; the endpoint: a document's suggestions with quotes and nothing saved, only saved pages fetched, an unreadable page explains why, another owner's document not found, one source at a time, sign-in needed, off without a key, the hourly limit; the approve follow-up. Migration `0027` upgrades and downgrades cleanly |
| Frontend | Typecheck and build pass; 101 tests pass (Node 22) with the calendar picker merged in, 4 new in `KnowledgeSuggestions.test.tsx`: each source read in turn with a failing one explained, add and skip, questions the owner already has hidden, stop when suggestions are unavailable, ask for a source when there is none |
| Live | Not deployed. Drafting hasn't been tried with a real OpenAI key; the request is checked against a stand-in. Page fetching was tried by hand against a public page over http and https |

## Filling in the profile from the owner's website (2026-11-22)

**Outcome:** an owner who gives their website on the first sign-up step no longer has to type everything again. It's read in the background as they go on: the home page and up to 4 of the site's own pages that look useful (services, prices, hours, contact, team). The next steps then offer what it says: the phone number, opening hours and address, services with prices and lengths, and the team. Each offer shows the words it came from, with a button to use it and **Not these**. Using it only fills the form, which is saved with the step like anything typed.

The server keeps only what the site says. Each item needs a quote that is on the site, and the phone digits, address numbers and opening and closing hours must be in that quote. A price or length whose number isn't in it is left blank for the owner. Pages are fetched with the same guards as the owner's listed pages, and a read counts against the same hourly limit. See [website-details.md](website-details.md).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 673 passed (local PostgreSQL 14), 12 new in `test_website_details.py`: which same-site pages are chosen (no other sites, images or the home page again, at most 4), the home page and useful pages read with a failing one skipped, an unreadable home page; only what the site says is kept (hours whose times aren't in the quote, repeated days and closing before opening dropped; prices and lengths not in the quote left blank; services and people not named in the quote dropped), a phone or address the site doesn't give, salons get one price row, the schema asks for this business type's sizes; the endpoint: details from the saved website with nothing saved, a website needed first, off without a key, a private address refused, the shared hourly limit |
| Frontend | Typecheck and build pass; 107 tests pass (Node 22), 6 new: the website is read after the first step and its hours and address are offered and used on the next, services added with blanks kept and ones already there not offered again, the phone offered only while empty, the team added or turned down, nothing shown once used, reading and failure with Try again |
| Live | Not deployed. Not tried with a real OpenAI key or on real business websites |

## Owners hear when they're live, in the voice they chose (2026-11-22)

**Outcome:** two gaps at the end of onboarding are closed.

- **The voice:** the sign-up profile asks for a female or male voice, but every business sounded like the deployment's default. A business now keeps its owner's choice (`businesses.voice`, migration `0028`, set by `provision approve` and the business file's `voice`). Website sessions and OpenAI phone calls use it ahead of `VOICE_NAME` and `PHONE_VOICE`. Vapi calls use `VAPI_MALE_VOICE_ID` (ElevenLabs "Adam") for `cedar`.
- **Being told:** nobody told the owner they were live. Approving now emails them "<business> is set up with HelloEver AI", with their booking page, staff dashboard and how to sign in, their calendar, and that their number is coming. Registering the business's first phone number emails "Your receptionist answers at <number>", with a test call to make and how to forward their own number to it. Replies go to `ADMIN_EMAIL`. See [email.md](email.md#going-live).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 684 passed (local PostgreSQL 14), 11 new in `test_go_live.py`: approve keeps the chosen voice; phone calls and browser sessions use the business voice over the deployment's; Vapi gets the male voice only from ElevenLabs; the approval email (owner address, Reply-To, links, calendar not connected, no number promised) and a connected calendar named; the first number emails the owner once (not again for the same or a second number); businesses without a sign-up and the demo get none; numbers written as people read them. Migration `0028` upgrades and downgrades cleanly |
| Frontend | Unchanged |
| Live | Not deployed. Needs `PUBLIC_SITE_URL` for full links; the Vapi male voice id not tried on a call |

## Sign-ups can go live on their own, for testing (2026-11-22)

**Outcome:** trying onboarding end to end needed you at the Railway console for every test sign-up. With `AUTO_APPROVE_SIGNUPS=true`, a profile goes live the moment it's submitted, exactly as `provision approve --yes` would. The phone number given at sign-up is registered to ring the new business, and the owner gets both go-live emails. For testing, owners give the demo line +1 (269) 699-2551, and the newest sign-up with it takes it. A number is only taken from the demo or an earlier sign-up (`business_phone_numbers.from_signup`, migration `0029`), never from a business you registered it for by hand. Off by default; it must be off before real customers sign up, since their own number isn't on our phone line. See [onboarding.md](onboarding.md#trying-onboarding-end-to-end-automatic-approval).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 690 passed (local PostgreSQL 14), 6 new in `test_auto_approve.py`: a sign-up goes live with its own number ringing it and both owner emails, and the admin notice says so; the newest sign-up with the demo line takes it from the demo and from the previous one; a number registered by hand is never taken; a number without an area code is skipped; a profile that can't be approved stays waiting; switched off, every sign-up waits. Migration `0029` upgrades and downgrades cleanly |
| Frontend | Unchanged |
| Live | Not deployed. Needs `AUTO_APPROVE_SIGNUPS=true` on Railway, and the demo line on the OpenAI trunk or Vapi as today |

## Profile layout fixes (2026-11-22)

**Outcome:** three things seen on the live profile are fixed. A long Google Calendar ID (`…@group.calendar.google.com`) ran out of the calendar box; text there now wraps inside it. A step name that wraps ("What the receptionist should know") had its number beside the second line; the number now sits on the first. Google's `iss` parameter stayed in the address bar after signing in with Google; it's now removed with the rest.

| Check | Result |
| --- | --- |
| Frontend | Typecheck and build pass; 107 tests pass (Node 22), the Google return test now includes `iss`. Checked in a browser with the built stylesheet at 1280 px and 375 px: the ID wraps inside its box, no sideways scrolling, step 6's number on its first line |
| Backend | Unchanged |
| Live | Not deployed |

## Calendars shown by name (2026-11-22)

**Outcome:** the profile and the staff dashboard showed a connected calendar by its ID, often a long `…@group.calendar.google.com` string. Both now show its name in Google Calendar ("Connected to **Shaggy Chic bookings**"). The access check already reads the calendar's list entry, so it now returns the name. Connecting saves it on the profile (`google_calendar_name`, server-set), **Check connection** refreshes it after a rename, and the dashboard gets it from its live check. A calendar connected before this shows "your Google Calendar" until checked. See [calendar.md](calendar.md#is-it-still-working).

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 694 passed (local PostgreSQL 14), 4 new in `test_calendar_connect.py`: the check returns the owner's name for the calendar (trimmed; none when Google gives none), connecting keeps it and a check refreshes it and disconnecting clears it, a profile edit can't set it, the dashboard status names the calendar and drops the name when the check fails |
| Frontend | Typecheck and build pass; 107 tests pass (Node 22): the profile shows the picked calendar's name, not its ID, after connecting by hand and with Google; the dashboard shows the name, not the ID |
| Live | Not deployed |

## The receipt matches what happened to the sign-up (2026-11-22)

**Outcome:** with automatic approval on, the owner still got "we'll contact you within one business day … goes live after that" right before "you're set up", and the "New sign-up" email said it was approved automatically even when approval failed. Now:

- **The receipt:** a sign-up approved at once gets only the go-live emails. One that can't be approved gets the usual receipt.
- **The "New sign-up" email to you:** it says what really happened. Either it's live at `/b/<slug>` and which number rings it (or that none does, with the command to add one), or why approval failed, with the approve command.
- **The greeting:** the receipt greets by first name, like the go-live emails.

| Check | Result |
| --- | --- |
| Backend lint and format | `ruff check`, `ruff format --check` clean |
| Backend tests | 691 passed (local PostgreSQL 14). In `test_auto_approve.py`: no receipt for an approved sign-up and the number named; no number explained with the add command; a failed approval gets the receipt and the reason with the approve command; the receipt greets by first name |
| Frontend | Unchanged |
| Live | Not deployed |
