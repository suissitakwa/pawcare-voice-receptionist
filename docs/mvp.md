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

## Roadmap

Evidence for finished work is in [progress.md](progress.md).

- [x] Website, API, database, automated checks
- [x] Service catalog, hours, and FAQs from the database
- [x] Live deployment
- [x] Availability from hours, breaks, buffers, and blocked times
- [x] Booking with database-enforced overlap prevention, idempotency, and bound confirmation
- [x] Non-voice booking form
- [x] Browser voice session with short-lived credentials, quota, and disable switch
- [x] Voice interface states, transcript, and error handling
- [x] Assistant tools backed by the API, including full voice booking
- [x] Protected staff dashboard: bookings, cancellation, blocked time, booking metrics
- [x] Callback requests and voice activity metrics on the dashboard
- [ ] Per-session summaries on the dashboard
- [x] Local test mode and scripted evaluation suite
- [ ] Results from a real evaluation run
- [ ] Hardening, accessibility review, and data retention
- [ ] Manual audio testing and latency measurement
- [ ] Evaluation report, demo script, and customer demonstration checklist
