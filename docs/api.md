# API

Interactive documentation is generated at `http://localhost:8000/docs` when the backend is running. This page records the contract as it is implemented.

## Errors

Application errors share one shape. Clients branch on `code`, and `message` is safe to display.

```json
{ "error": { "code": "database_unavailable", "message": "The service is temporarily unavailable." } }
```

## Endpoints

### `GET /health`

Liveness. `200 {"status": "ok"}`. Does not touch the database.

### `GET /health/ready`

Readiness.

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "ready", "database": "ok", "schema_revision": "0006"}` | Safe to serve |
| 503 | `{"status": "unavailable", "database": "unreachable", "schema_revision": null}` | Database cannot be reached |
| 503 | `{"status": "unavailable", "database": "ok", "schema_revision": "<found>"}` | Migrations have not been applied for this build |

### `GET /api/v1/business`

Public profile of the business this deployment serves. The record is chosen by server configuration, and request parameters cannot change it.

| Status | Meaning |
|---|---|
| 200 | `name`, `tagline`, `about`, `timezone`, `contact_email`, `contact_phone`, `address`, `is_demo`, `hours` |
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

`closed` means the business is shut or the groomer is not working. An empty `slots` list with `closed: false` means the day is fully taken.

| Status | Code | Meaning |
|---|---|---|
| 404 | `unknown_service` | No active service with that name for this size |
| 404 | `unknown_size` | No such dog size |
| 422 | `staff_assessment_required` | This size must speak to staff first |
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

`starts_at` must include a UTC offset and match a start that `GET /api/v1/availability` would offer. An email address or a phone number is required. Unknown fields, including any attempt to send a price, are rejected.

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
| `POST /api/v1/staff/login` | `{email, password}` returns `token`, `expires_at` (8 hours), and `staff`. Wrong details return `401 invalid_credentials`. Five failures return `429 too_many_attempts` for 15 minutes |
| `POST /api/v1/staff/logout` | Revokes the token. `204` |
| `GET /api/v1/staff/me` | The signed-in staff member |
| `GET /api/v1/staff/bookings?scope=upcoming` | Bookings with customer contact details. `scope=past` lists finished bookings, most recent first |
| `POST /api/v1/staff/bookings/{reference}/cancel` | Cancels and frees the time. Repeating it is harmless. `404 booking_not_found` |
| `GET /api/v1/staff/blocks` | Current and future blocked periods |
| `POST /api/v1/staff/blocks` | `{start_date, start_time, end_date, end_time, reason}` as wall-clock values in the business time zone. Returns the block and any confirmed bookings it overlaps. `422 invalid_block` for an empty, past, or over-31-day range |
| `DELETE /api/v1/staff/blocks/{id}` | Removes a block. `204`, or `404 block_not_found` |
| `GET /api/v1/staff/metrics` | Upcoming bookings, bookings made in the last 7 days, and cancellations in the last 7 days |

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

`{session_token, reason, usage}` records how the session ended: `user_ended`, `timeout`, `error`, `disconnected`, `mic_denied`, or `page_closed`. `usage` holds token counts reported by the provider. Repeating it is harmless. Returns `204`.
