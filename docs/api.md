# API

Interactive documentation is generated at `http://localhost:8000/docs` when the backend is running. This page records the contract as it is implemented.

## Errors

Application errors share one shape. Clients branch on `code`, and `message` is safe to display.

```json
{ "error": { "code": "database_unavailable", "message": "The service is temporarily unavailable." } }
```

## Choosing the business

The public routes (business, services, FAQs, availability, bookings, voice sessions) are served at two addresses:

- **`/api/v1/b/{business_slug}/...`** serves the business with that slug, for example `/api/v1/b/pawcare-demo/services`. An unknown or malformed slug returns `404 business_not_found`.
- **`/api/v1/...`** (no slug) serves the deployment's own business, set by `BUSINESS_SLUG`. The demo site uses these. They stay until the frontend moves to slugged addresses.

Everything a request reads or changes belongs to that business. A booking summary, voice session or booking from one business can't be used through another business's address. Each business shows its own receptionist number (`assistant_phone`). Staff sign in at their business's address; after that, every staff route serves the signed-in staff member's business. Phone calls (OpenAI and Vapi) go to the business whose number was dialed; see [phone.md](phone.md#numbers-for-several-businesses). See [decision 0010](decisions/0010-multiple-businesses.md).

The endpoints below are written without the slug.

## Endpoints

### `GET /health`

Liveness. `200 {"status": "ok"}`. Does not touch the database.

### `GET /health/ready`

Readiness.

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "ready", "database": "ok", "schema_revision": "0007"}` | Safe to serve |
| 503 | `{"status": "unavailable", "database": "unreachable", "schema_revision": null}` | Database cannot be reached |
| 503 | `{"status": "unavailable", "database": "ok", "schema_revision": "<found>"}` | Migrations have not been applied for this build |

### `GET /api/v1/business`

Public profile of the business this deployment serves. The record is chosen by server configuration, and request parameters cannot change it.

| Status | Meaning |
|---|---|
| 200 | `name`, `tagline`, `about`, `timezone`, `contact_email`, `contact_phone`, `address`, `is_demo`, `hours`, `assistant_phone` (the business's first registered receptionist number in E.164, or `PHONE_NUMBER` for the deployment's own business; `null` when it has none or the phone line isn't answering) |
| 404 | `business_not_configured`: the demo seed has not been run |
| 503 | `database_unavailable` |

`hours` holds one entry per open day: `weekday` (0 is Monday), `opens_at`, and `closes_at`. These are wall-clock times in the business time zone. A day with no entry is closed.

### `GET /api/v1/services`

Active services, each with a duration and indicative price for every eligible size.

```json
{
  "size_categories": [
    { "slug": "small", "label": "Small", "description": "Under 25 lb", "requires_staff_assessment": false }
  ],
  "services": [
    {
      "slug": "full-groom",
      "name": "Full Groom",
      "description": "...",
      "options": [{ "size": "small", "duration_minutes": 90, "price_cents": 7000 }]
    }
  ]
}
```

A size with `requires_staff_assessment: true` never has an option, so it cannot be priced or booked directly.

A business with more than one active staff member also returns `staff`, so a customer can ask for someone: `[{ "slug": "alex", "name": "Alex", "services": ["full-groom", "nail-and-paw"] }]`, where `services` are the service slugs that person does. With one staff member it is `[]`.

### `GET /api/v1/faqs`

Approved questions and answers, in display order: `slug`, `question`, `answer`.

Both endpoints return `404 business_not_configured` or `503 database_unavailable` under the same conditions as `/api/v1/business`.

### `GET /api/v1/availability`

Open appointment starts for one service, dog size, and local date in the business time zone. This is read-only and does not hold a slot, so the booking step must recheck it.

| Query parameter | Example |
|---|---|
| `service` | `full-groom` |
| `size` | `medium` |
| `date` | `2026-11-10` |
| `staff` (optional) | `alex`: only this person's open times. Leave it out for anyone who does the service |

```json
{
  "date": "2026-11-10",
  "timezone": "America/Los_Angeles",
  "service": "full-groom",
  "size": "medium",
  "duration_minutes": 120,
  "closed": false,
  "slots": [
    {
      "starts_at": "2026-11-10T17:00:00Z",
      "ends_at": "2026-11-10T19:00:00Z",
      "local_start": "2026-11-10T09:00:00-08:00",
      "local_end": "2026-11-10T11:00:00-08:00"
    }
  ]
}
```

`closed` means the business is shut or nobody who does the service is working. An empty `slots` list with `closed: false` means the day is fully taken. With several staff, a start is listed while anyone who does the service is free for it.

The staff errors below also apply to `POST /api/v1/bookings/prepare`.

| Status | Code | Meaning |
|---|---|---|
| 404 | `unknown_service` | No active service with that name for this size |
| 404 | `unknown_size` | No such dog size |
| 422 | `staff_assessment_required` | This size must speak to staff first |
| 404 | `unknown_staff` | No active staff member with that slug |
| 422 | `staff_not_qualified` | That person doesn't do this service |
| 422 | `date_in_past` | The date has passed in the business time zone |
| 422 | `date_too_far_ahead` | The date is past the booking horizon |
| 422 | `invalid_request` | A parameter is missing or malformed |
| 503 | `database_unavailable` | The database cannot be reached |

### Scheduling rules

- A start must leave the appointment inside both business hours and groomer hours.
- The appointment cannot overlap a break or blocked period.
- Each booking occupies its appointment plus a trailing buffer. A new appointment and its own buffer must clear every existing booking. The buffer may run past closing time or into a break.
- Starts step by the configured increment from the opening time.
- Starts must respect the minimum notice. Dates must fall within the booking horizon.
- Comparisons use UTC. Wall-clock hours are converted per date, so appointments have their true elapsed length across daylight-saving changes.

## Booking

Booking takes two steps, so nothing is booked until the customer has seen and accepted the exact details. Every field in a request is treated as untrusted, including when the assistant sends it. Prices, durations, and availability always come from the database.

### `POST /api/v1/bookings/prepare`

Checks the slot and returns a summary to show the customer. Nothing is booked yet.

```json
{
  "service": "full-groom",
  "size": "medium",
  "starts_at": "2026-11-10T09:00:00-08:00",
  "owner_name": "Jordan Example",
  "owner_email": "jordan@example.com",
  "owner_phone": null,
  "pet_name": "Biscuit"
}
```

`starts_at` must include a UTC offset and match a start that `GET /api/v1/availability` would offer. An email address or a phone number is required. An optional `staff` slug books that person; without it the booking goes to whoever is free, the one with fewest bookings that day first. At a business with several staff, the summary's `staff` names who it is with (otherwise `null`), and confirming fails with `slot_unavailable` if that person has been taken in the meantime. Unknown fields, including any attempt to send a price, are rejected.

`201` returns `confirmation_token`, `summary_hash`, `expires_at` (10 minutes), and `summary`: service, size, pet and owner details, UTC and local times, duration, and price.

### `POST /api/v1/bookings/confirm`

Books exactly the prepared summary. Requires an `Idempotency-Key` header of 8 to 128 characters.

```json
{ "confirmation_token": "...", "summary_hash": "..." }
```

| Status | Meaning |
|---|---|
| 201 | Booked. Returns `reference`, `status`, and the `summary` |
| 200 | A retry with the same key and token. Returns the original booking, with header `Idempotent-Replayed: true` |

### Booking errors

| Status | Code | Meaning |
|---|---|---|
| 404 | `confirmation_not_found` | Unknown confirmation token |
| 409 | `slot_unavailable` | The time is no longer free, or never was |
| 409 | `already_confirmed` | This summary was already booked under a different key |
| 409 | `confirmation_mismatch` | The summary hash does not match what was prepared |
| 410 | `confirmation_expired` | The summary is older than 10 minutes. Prepare again |
| 422 | `idempotency_key_reused` | The key was used for a different request |

Prepare also returns the service, size, and date errors listed under availability.

No public endpoint lists, reads, changes, or cancels bookings. Only signed-in staff can, through the staff endpoints below.

## Staff

Every staff endpoint except sign-in requires `Authorization: Bearer <token>`, and returns `401 not_authenticated` without a valid, unexpired, unrevoked token. Results are limited to the signed-in staff member's business. See [decision 0004](decisions/0004-staff-authentication.md).

| Method and path | Purpose |
|---|---|
| `POST /api/v1/b/{business_slug}/staff/login` | Signs in to the named business. `{email, password}` returns `token`, `expires_at` (8 hours), and `staff`. Wrong details, including staff of a different business, return `401 invalid_credentials`. Five failures return `429 too_many_attempts` for 15 minutes. An unknown slug returns `404 business_not_found` |
| `POST /api/v1/staff/login` | The same, for the deployment's own business (`BUSINESS_SLUG`) |
| `POST /api/v1/staff/logout` | Revokes the token. `204` |
| `GET /api/v1/staff/me` | The signed-in staff member: `email`, `display_name`, `business_slug`, `business_name`, `business_is_demo`. The login response's `staff` has the same shape |
| `GET /api/v1/staff/bookings?scope=upcoming` | Bookings with customer contact details, `source` (`form` or `voice`), `channel` (`form`, `phone` or `website`), `changed_at` (when the customer last changed it by phone or on the website) and `cancelled_by` (`staff` or `customer`). `scope=past` lists finished bookings, most recent first |
| `GET /api/v1/staff/calls?limit=50` | The latest conversations with the receptionist, newest first (`limit` 1 to 200): when, `channel` (`phone` or `website`), `test_mode`, `in_progress`, `duration_seconds`, `ended_by` in plain words, `booked_reference`, `changed_reference` and `cancelled_reference` for what the caller did on the call, `callback_requested`, `message_only`, `tool_failures`, and `cost_usd` when the provider reports one (Vapi). No audio or transcript is kept |
| `POST /api/v1/staff/bookings/{reference}/cancel` | Cancels and frees the time. Repeating it is harmless. `404 booking_not_found` |
| `GET /api/v1/staff/team` | Active staff members: `slug`, `name` |
| `GET /api/v1/staff/blocks` | Current and future blocked periods, each with `staff`: the person's name, or `null` for the whole business |
| `POST /api/v1/staff/blocks` | `{start_date, start_time, end_date, end_time, reason, staff}` as wall-clock values in the business time zone. `staff` is a staff slug, or left out to close the whole business. Returns the block and any confirmed bookings it overlaps. `422 invalid_block` for an empty, past, or over-31-day range; `404 unknown_staff` |
| `DELETE /api/v1/staff/blocks/{id}` | Removes a block. `204`, or `404 block_not_found` |
| `GET /api/v1/staff/metrics` | Upcoming bookings, bookings and cancellations in the last 7 days, voice calls and voice bookings in the last 7 days, open callbacks, assistant tool failures in the last 7 days, and the median and 90th percentile voice reply time in the last 7 days (null until a call is measured) |
| `GET /api/v1/staff/callbacks?status=open` | Callback requests, newest first. `status` is `open`, `done`, or `all` |
| `POST /api/v1/staff/callbacks/{id}/done` | Marks a callback as handled. Repeating it is harmless |
| `GET /api/v1/staff/webhooks` | Whether the automation webhook is configured (and if not, why), events waiting, sent and failed in the last 7 days, and the last error. Never returns the URL or secret |
| `POST /api/v1/staff/webhooks/test` | Queues a `webhook.test` event and returns its id. See [automations.md](automations.md) |

## Subscribers

Business owners sign up and fill in the profile that will configure their receptionist. Pet grooming, hair salon, and barbershop businesses are supported. See [decision 0005](decisions/0005-subscriber-profiles.md).

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/subscribers/options` | Business types, the two voices (one female, one male), calendars, and document limits |
| `POST /api/v1/subscribers/signup` | `{full_name, email, password, accept_terms}` creates an account and an empty draft profile, and returns a session token. Passwords need 12 characters. `409 email_taken` if the email is registered; `429` after 5 sign-ups from one address in an hour |
| `POST /api/v1/subscribers/login` | `{email, password}` returns a token. Five failures lock the account for 15 minutes |
| `POST /api/v1/subscribers/logout` | Ends the session |
| `GET /api/v1/subscribers/me` | The signed-in account |
| `GET /api/v1/subscribers/profile` | The profile, its documents, and `missing`: what must be filled in before submitting. `business_slug` is set once the receptionist is live at `/b/{business_slug}` |
| `PATCH /api/v1/subscribers/profile` | `{business_name?, business_type?, details?}`. Detail fields sent replace the stored ones; others are kept. The merged result is validated, and errors name the fields |
| `POST /api/v1/subscribers/profile/submit` | Marks the profile submitted, or `422 profile_incomplete` listing the missing sections |
| `POST /api/v1/subscribers/profile/documents?filename=…` | The body is the raw file, with its `Content-Type`: PDF, .docx, plain text, or Markdown, up to 5 MB, 10 per profile |
| `DELETE /api/v1/subscribers/profile/documents/{id}` | Removes a document |
| `GET /api/v1/staff/plan`, `POST /api/v1/staff/plan/request` | The business's free trial or plan and this month's calls; asking to keep the receptionist emails you. See [pricing.md](pricing.md) |
| `POST /api/v1/subscribers/profile/website/read` | No body. Reads the saved website (home page and up to 4 of its own pages) and returns the phone, address, hours, services, team, mobile area, time zone and answers to common questions it states, each with its quote. Nothing is saved. See [website-details.md](website-details.md) |
| `POST /api/v1/subscribers/profile/knowledge/suggest` | `{"url": …}` (a saved page) or `{"document_id": …}`. Returns suggested answers drafted from that one source, each with the quote it rests on. Nothing is saved. See [knowledge.md](knowledge.md) |

Every profile endpoint needs `Authorization: Bearer <token>` and only reaches the signed-in account's profile.

## Voice

### `POST /api/v1/voice/sessions`

Starts a voice session. The server applies the off switch, a daily quota in the business time zone, and a per-visitor hourly limit before anything else.

| Field | Meaning |
|---|---|
| `session_id`, `session_token` | Identify the session when ending it. The token is stored only as a hash |
| `mode` | `test` (scripted preview, no provider) or `live` |
| `client_secret` | Live only. A short-lived OpenAI credential, valid for 60 seconds to start the call. The permanent key never leaves the server |
| `max_duration_seconds` | When the browser ends the call |

| Status | Code | Meaning |
|---|---|---|
| 503 | `voice_disabled` | `VOICE_MODE=off`, or live mode without a key |
| 429 | `voice_quota_reached` | Today's session limit is used up |
| 429 | `too_many_requests` | This visitor started too many sessions in the last hour |
| 502 | `voice_unavailable` | OpenAI could not issue a credential. No quota is used |

### `POST /api/v1/voice/sessions/{id}/end`

`{session_token, reason, usage}` records how the session ended: `user_ended`, `timeout`, `error`, `disconnected`, `mic_denied`, or `page_closed`. `usage` holds token counts reported by the provider, reply counts, and `reply_latency_ms`, the browser-measured time from the end of each caller turn to the start of the reply's audio (at most 200 values, each 0 to 120000). Repeating it is harmless. Returns `204`.

### `POST /api/v1/voice/sessions/{id}/tools/{name}`

Runs one assistant tool. The browser relays the model's request: `{session_token, arguments}`, where `arguments` is the model's raw JSON string. The backend parses and validates it as untrusted input.

| Tool | Does |
|---|---|
| `check_availability` | `service`, `size`, `date`, and `staff` at a business with a choice of staff. Returns up to 12 open times with labels and `starts_at`, plus duration and demo price |
| `prepare_booking` | `service`, `size`, `starts_at`, `owner_name`, `pet_name`, and a phone or email. Returns a summary for the model and, separately, the prepared booking for the screen. The confirmation token is never in the model's part |
| `create_callback_request` | `name`, a phone or email, `reason`, optional `note`. Saves a request for staff |
| `prepare_change` | A new `starts_at`, `service` or `size`, or a corrected `owner_name` or `pet_name`, for the latest booking confirmed in this session. Returns the old and new details for the model and, separately in `manage`, the same with a `confirm_token` for the screen. Changes nothing |
| `prepare_cancel` | No arguments. Shows the latest booking confirmed in this session with a cancel button, through `manage` like `prepare_change`. Cancels nothing |

The model has no confirm tool. A booking is made only when the visitor presses Confirm booking, which calls `POST /api/v1/bookings/confirm`. A change or cancellation is made only when the visitor presses Confirm change or Cancel booking, which calls the endpoint below. The browser then tells the model the result. Only a booking confirmed earlier in the same session can be changed or cancelled here (`no_booking_in_this_conversation` otherwise).

| Response | Meaning |
|---|---|
| `200` with `ok: true` | The tool ran. `result` goes to the model |
| `200` with `ok: false` | The tool ran but the request was refused, for example `slot_unavailable` or `invalid_arguments`. Counted as a tool failure |
| `403 voice_session_closed` | The session ended or passed its time limit plus a 60-second grace period |
| `404 voice_session_not_found` | Wrong session or token |
| `429 too_many_tool_calls` | The session used its budget of 40 tool calls |

### `POST /api/v1/voice/sessions/{id}/manage/confirm`

The visitor pressed Confirm change or Cancel booking. `{session_token, confirm_token}`, the token from the last `manage`. Applies exactly what was shown, once, within 10 minutes of showing it. Returns the same shape as a tool: `ok: true` with `status` `changed` (and the new details) or `cancelled`, or `ok: false` with `change_expired`. Webhooks get `booking.changed` or `booking.cancelled`, and the calendar is updated, as for a phone change.

| Response | Meaning |
|---|---|
| `409 nothing_to_confirm` | Wrong token, already used, or replaced by a newer change on screen |
| `409 slot_unavailable` | The new time was taken after it was shown |
| `403`, `404`, `429` | As for tools |

## Phone

### `POST /api/v1/phone/openai-events`

OpenAI's webhook for phone calls, verified with `OPENAI_WEBHOOK_SECRET` (Standard Webhooks signature; deliveries older than 5 minutes are refused). Returns 404 until the secret is set, and 400 for a bad signature.

- `realtime.call.incoming`: the call is answered (`{"status": "answered"}`) or declined (`{"status": "declined"}`: phone off, voice not live, a limit reached, a retried delivery, or accept failed).
- Any other event: `{"status": "ignored"}`.

Phone sessions get one extra tool, `confirm_booking` (`{"caller_confirmed": true}`), which books the summary from the session's last successful `prepare_booking`. See [phone.md](phone.md).


### `POST /api/v1/vapi/events`

The server URL for calls answered by Vapi. Every request must carry `VAPI_SECRET`, as `Authorization: Bearer <secret>` or `X-Vapi-Secret: <secret>`. Returns 404 until the secret is set, and 401 without it.

- `assistant-request`: `{"assistant": {...}}` with the phone instructions, tools, voice and transcriber, or `{"error": "..."}` (spoken to the caller) when Vapi calls are off or a phone limit is reached.
- `tool-calls`: `{"results": [{"toolCallId", "name", "result"}]}`, where `result` is the tool's JSON output as a string.
- `end-of-call-report`, and `status-update` with status `ended`: ends the session and stores Vapi's reported cost and duration.
- Anything else: `{}`.

Phone tools on both providers also include `find_booking`, `prepare_change`, `confirm_change` and `cancel_booking`. See [vapi.md](vapi.md).
