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
| 200 | `{"status": "ready", "database": "ok", "schema_revision": "0002"}` | Safe to serve |
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
