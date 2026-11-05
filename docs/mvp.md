# PawCare Voice Receptionist: MVP

## What this is

A demonstration website for a fictional dog grooming business, with a browser-based voice receptionist that answers questions and books real appointments in a real database. A conventional booking form covers everyone voice can't.

It has three jobs:

1. Be demonstrated to real grooming businesses to recruit a pilot customer.
2. Build backend, AI integration, reliability, evaluation, and deployment skills.
3. Stand as a public portfolio project with a live demo and measured results.

## The core journey

Open the site → click "Talk to our assistant" → grant microphone access → ask questions → choose a service → give owner and pet details → hear available appointments → review a booking summary → explicitly confirm → receive a booking reference.

## In scope

| Area | Scope |
|---|---|
| Business | One fictional business, one location (America/Los_Angeles), one groomer, dogs only, English only |
| Catalog | Three services with configured durations and indicative demo prices, by size category |
| Website | Home, services and pricing, about, FAQs, contact, booking. Responsive, accessible, labelled as a demo |
| Voice | Browser only. Start, mute, end. Listening, thinking, speaking, disconnected, error states. Live transcript. Interruption and correction |
| Fallback | Non-voice booking form that works when voice is off or unavailable |
| Dashboard | Protected: bookings, callbacks, session outcomes, blocked times, cancellation, basic metrics |

## Out of scope for this release

Telephone numbers, external calendar sync, payments, SMS or email delivery, multiple groomers or locations, other species, other languages, customer accounts, microservices, Kafka, Kubernetes, multi-agent frameworks, vector databases. FAQs stay structured; RAG is added only if the content proves to need it.

## Rules that do not bend

- PostgreSQL is the booking source of truth. The model never decides whether a slot is free or a booking exists.
- Overlaps are prevented by the database, including under simultaneous requests.
- A booking requires explicit confirmation bound to the exact service, pet details, and slot. Any change requires reconfirmation.
- The assistant says "confirmed" only after a successful backend result.
- User speech and website content are data, never permission to bypass backend rules.
- Permanent provider keys never reach the browser or logs. Raw audio is not recorded by default.
- Simulated results are never presented as real voice measurements. Published metrics must be reproducible from this repository.

## Architecture

See [architecture.md](architecture.md) and the [decision records](decisions/).

## 15-day plan

Each day ends with something running, evidence in [progress.md](progress.md), and a suggested commit. Deployment and real voice are deliberately early because they carry the most unknowns.

| Day | Outcome | Proof |
|---|---|---|
| 1 | Website shell, health endpoints, Postgres, migrations, local setup, CI | Browser renders data read from Postgres |
| 2 | **First deployment.** Live backend, managed Postgres, Netlify frontend, then catalog schema: services, size rules, hours, FAQs | Live URL renders database content; readiness passes in production |
| 3 | Availability engine: hours, breaks, buffers, blocks, durations, time zone | Boundary tests, including daylight-saving changes |
| 4 | Booking core: exclusion constraint, idempotency keys, prepare and confirm, confirmation binding | Concurrency test: N simultaneous requests, exactly one succeeds |
| 5 | Non-voice booking form, end to end, live | Book on the live site; conflict shown when a slot is taken |
| 6 | **First real voice call.** Ephemeral credentials, WebRTC connection, quota, disable switch, rate limit | Real spoken conversation on the deployed site |
| 7 | Voice UI: all states, transcript, mute, end, permission and connection errors, track cleanup | Manual checklist, including microphone denied |
| 8 | Tools wired to the backend: business info, services, FAQ, availability, callback | Voice answers from database records; unknown FAQ falls back |
| 9 | Full voice booking: visible summary, confirm control, correction, interruption | Real spoken booking appears in the database with a reference |
| 10 | Staff authentication and dashboard: bookings, callbacks, blocks, cancellation | Authorization tests; a block removes a slot from voice and form |
| 11 | Session outcomes, summaries, metrics, usage and cost capture | Metrics match database counts |
| 12 | Local test mode without paid calls; 30+ scenarios; scripted evaluation runner | First evaluation report, labelled as scripted |
| 13 | Hardening: rate limits, failure paths, accessibility pass, retention and cleanup | Fault-injection tests; keyboard-only and contrast checks |
| 14 | Manual audio testing and latency measurement with a defined method | Logged real sessions, reported apart from scripted results |
| 15 | Final evaluation report, documentation, demo script, customer checklist, release | Fresh clone reproduces; two-minute demo rehearsed on live site |

**Buffer rule:** if the schedule slips, cut in this order: dashboard polish, cost reporting detail, then scripted scenarios beyond the required 30. Booking correctness, real voice, and the form are never cut.
