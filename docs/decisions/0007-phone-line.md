# 0007: Phone calls through OpenAI's SIP connection

## Context

A receptionist has to answer a real phone number. Hosted voice platforms such as Retell or Vapi provide phone numbers and call handling, but add a per-minute platform charge and take over the booking flow. This project already has the booking rules, the tools and the instructions, built on OpenAI Realtime.

## Decision

- Calls reach OpenAI Realtime directly over SIP from a carrier trunk (Twilio). OpenAI's signed `realtime.call.incoming` webhook lets the backend accept or decline each call.
- The backend joins each call over a server-side WebSocket and runs the tools itself, with the same validation and limits as browser tools. Calls are tracked as voice sessions with `channel = 'phone'` (migration `0010`).
- Bookings are confirmed out loud. A `confirm_booking` tool, available only to phone sessions, books the summary the server prepared. The confirmation token stays on the server, so the model can report the caller's yes but not change what is booked.
- Phone calls have their own daily and per-caller limits and a hard time limit, and are off unless `PHONE_ENABLED` is set.

## Consequences

- No platform charge per minute. Costs are model usage plus the carrier.
- One code path for availability, booking and callbacks across web and phone.
- The backend now holds long-lived WebSocket connections. A redeploy ends calls in progress; they are recorded as `server_stopped`.
- The guarantee "a booking needs the customer's explicit action" changes from a click to a spoken yes after a read-back. That relies on the model reporting the yes honestly, which is why the server, not the model, decides what gets booked.
- Hosted platforms would still give faster setup and built-in call analytics. The tools are plain HTTP-style functions, so moving to one later remains possible.
