# 0006: Automation webhooks

## Context

Nobody is told when a booking is made or a callback is requested. A business owner will not watch a dashboard, so a real business cannot rely on the product without notifications. Email and text delivery, reminders, and calendar copies are better built in an automation tool such as n8n than in the backend.

## Decision

- **One signed webhook.** The backend sends `booking.confirmed`, `booking.cancelled`, `callback.requested`, and `webhook.test` events to one URL, signed with HMAC-SHA256 over the timestamp and the raw body. The URL must use https and the secret must have at least 32 characters, or nothing is sent.
- **Transactional outbox.** Each event is a row written in the same transaction as the change it describes. A worker in the API process sends due rows every 5 seconds, using `FOR UPDATE SKIP LOCKED` so more than one process could share the work without sending twice.
- **At least once, with an id.** Failures are retried with exponential backoff, from 30 seconds to at most an hour, 8 attempts in all. Every attempt carries the same event id, so the receiver removes duplicates.
- **Late is worse than never.** An event not delivered within 24 hours is marked stale and not sent. Events are deleted after 30 days because they hold contact details.

## Consequences

- No new infrastructure: the outbox is a table and the worker runs inside the existing service.
- Customer contact details leave the backend for the automation tool, which then has to be secured and covered by the privacy policy.
- One webhook serves the whole deployment. Subscriber businesses will each need their own, with their own secret, when their receptionists go live.
