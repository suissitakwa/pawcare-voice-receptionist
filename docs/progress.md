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
