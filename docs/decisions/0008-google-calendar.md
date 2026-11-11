# 0008: Two-way Google Calendar sync through a service account

## Context

Owners keep their real schedule in Google Calendar. The receptionist must not offer a time the owner is busy, and bookings should show up where the owner already looks.

## Decision

- **Service account, shared calendar.** The owner shares one calendar with a service account. No OAuth consent screen, no refresh tokens to store, and access ends when the owner unshares. Per-subscriber OAuth can come later with profile-driven receptionists.
- **Read with free/busy.** Busy intervals for the day are added to the existing blocked periods, so every rule (hours, breaks, buffers, notice) still applies in one place. Cached for 60 seconds.
- **Write through an outbox on the booking.** A booking is marked for sync whenever it is created, changed or cancelled; a worker pushes it with a deterministic event id. Bookings never wait on Google.
- **Our events are "free".** So reading busy times never counts our own bookings twice, and a booking can move within its own time.
- **Fail closed on reads.** If the calendar can't be read, booking stops with a clear error instead of risking a clash.
- **Signing with `cryptography`** instead of Google's client libraries: one small dependency, and the HTTP calls are three endpoints.

## Consequences

- Availability checks make one Google request per day checked (then cached), adding a little latency to the first check.
- A Google outage stops new bookings while it lasts. Existing bookings are unaffected.
- Events are created only for the business's one groomer calendar. Multiple calendars per business are not supported yet.
