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
| 200 | `{"status": "ready", "database": "ok", "schema_revision": "0003"}` | Safe to serve |
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
