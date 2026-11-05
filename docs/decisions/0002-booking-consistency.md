# 0002: Booking consistency

**Status:** Accepted. Not yet implemented.

## Context

Voice and form users can race for the same slot. A check-then-insert in application code cannot prevent double booking, because two transactions can both see the slot free. Network retries and repeated tool calls can also create duplicates.

## Decision

1. **A PostgreSQL exclusion constraint is the overlap guarantee.** Bookings store a UTC `tstzrange` that includes any buffer, with `EXCLUDE USING gist (groomer_id WITH =, slot WITH &&) WHERE (status = 'confirmed')`. `btree_gist` was enabled in migration 0001 so that host support is proven on first deploy.
2. **Availability is rechecked in the booking transaction,** but only to give friendly errors. The constraint remains the guarantee, and a violation maps to a clear `slot_unavailable` conflict response.
3. **Idempotency keys are unique in the database.** Replaying a key returns the original result. Reusing a key with a different payload is an error.
4. **Two-step commit.** `prepare_booking` stores a server-side draft with a short expiry and a hash of service, pet details, and slot. `confirm_booking` commits only that exact draft. Any change means a new draft and a new confirmation.
5. **Timestamps are stored in UTC** and shown in the business time zone. Local wall-clock hours are converted per date, so daylight-saving changes are handled.

## Alternatives rejected

- **Application locks or Redis:** add infrastructure and fail if any code path skips them.
- **`SERIALIZABLE` isolation alone:** works, but needs retry handling everywhere and leaves the rule implicit. The constraint states it in the schema.
- **Pre-generated slot rows:** make uniqueness simple but make changes to hours, durations, and buffers painful.

## Consequences

Blocked periods stay in their own table, outside the exclusion constraint, because they are staff-entered ranges and not bookings. The booking transaction rechecks them. A staff member can create a block that overlaps a booking made moments earlier, and the dashboard must show that conflict. Concurrency tests must run on real PostgreSQL, so tests never substitute SQLite.
