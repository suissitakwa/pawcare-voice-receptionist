# Phone line

Callers dial a normal phone number and talk to the same receptionist as the website: same model, voice, instructions, availability and booking rules. No voice platform sits in between, so the costs are OpenAI Realtime usage plus the phone carrier's number and per-minute charges.

## How a call flows

```
Caller ──► Twilio number ──SIP──► OpenAI Realtime ──webhook──► backend
                                        ▲                        │
                                        └── accept + WebSocket ◄─┘
```

1. Twilio forwards the call over SIP to the OpenAI project.
2. OpenAI sends a signed `realtime.call.incoming` webhook to `POST /api/v1/phone/openai-events`.
3. The backend checks the signature and its limits, then **accepts** the call with the instructions, voice and phone tools, or **declines** it.
4. The backend joins the call over a server-side WebSocket and runs every tool itself. The model speaks first.
5. When the caller hangs up, the session is ended and its token use is recorded. A call that reaches the time limit is hung up.

## Booking by phone

There is no screen, so the caller confirms out loud:

1. `check_availability` and `prepare_booking` work as on the website, including the read-back of names and contact details.
2. The assistant reads the summary (service, date, time, price) and asks whether to book it.
3. Only after a clear yes does it call `confirm_booking` with `caller_confirmed: true`.
4. The backend books **the summary it prepared**. The confirmation token never leaves the server, so the model can only say whether the caller agreed. It cannot change what is booked.
5. The reference is read back one character at a time.

Confirming twice returns the same booking. A browser session can't use `confirm_booking`: there it is an unknown tool.

## Limits and privacy

| Setting | Default | Effect |
| --- | --- | --- |
| `PHONE_ENABLED` | `false` | Off: every call is declined (SIP 603) |
| `PHONE_NUMBER` | empty | The number callers dial, in E.164 (`+12605550123`). While calls are answered, the website shows a **Call our assistant** button for it |
| `PHONE_MAX_CALL_SECONDS` | `600` | The backend hangs up after this |
| `PHONE_DAILY_CALL_LIMIT` | `50` | Calls answered per business day; later calls get busy (486) |
| `PHONE_CALLS_PER_CALLER_PER_HOUR` | `4` | Per calling number; later calls get busy (486) |

- Phone calls have their own daily limit and don't use the website's voice quota.
- The caller's number is offered to the assistant so it can propose using it, but it is always read back first. The database keeps only a hash of the number, for the per-caller limit.
- Transcripts are not stored.
- The assistant says it is an AI in its greeting and never claims to be a person.

## Setup

Phone calls need `VOICE_MODE=live` and `OPENAI_API_KEY`, as for browser voice.

### 1. OpenAI webhook

1. In the OpenAI dashboard, open the project that owns `OPENAI_API_KEY`. Note its project id (`proj_…`).
2. In the project's webhook settings, add an endpoint:
   - URL: `https://<backend host>/api/v1/phone/openai-events`
   - Event: `realtime.call.incoming`
3. Copy the signing secret (`whsec_…`). It's shown once.

### 2. Railway variables

On the backend service, set:

- `OPENAI_WEBHOOK_SECRET`: the signing secret
- `PHONE_ENABLED`: `true`
- `PHONE_NUMBER`: the Twilio number in E.164, for the website's call button (optional)

Redeploy. Until the secret is set, the endpoint returns 404.

### 3. Twilio number

1. Buy a number with voice capability.
2. Create an Elastic SIP Trunk. Under **Origination**, add the URI `sip:<project id>@sip.api.openai.com;transport=tls`.
3. Under the trunk's **Numbers**, attach the number.

A business can keep its own number and forward calls to this one, for example after hours or when nobody answers.

### 4. Check it

1. Call the number. The assistant should greet you within a couple of seconds and say it is an AI.
2. Ask for a time, book it, and say yes when it reads the summary.
3. In the staff dashboard: the booking appears with its reference, **Phone calls in the last 7 days** goes up by one, and the n8n workflow (if connected) receives `booking.confirmed`.

If nothing answers, check that the webhook endpoint shows a delivery in the OpenAI dashboard, then the backend logs for `pawcare.phone`. A delivery with a 400 status means the signature failed (wrong secret). If the call is accepted but the WebSocket won't connect, check the OpenAI account has credit.

## Not done yet

- One number per deployment: calls are answered for `BUSINESS_SLUG`. Routing each subscriber's number to their own profile comes with profile-driven receptionists.
- No transfer to a person (OpenAI's `refer` endpoint supports it).
- No phone reply-time measurement. Browser calls measure it; the server-side WebSocket doesn't get the audio playback events used for that.
- Not yet tested with a real phone call. The flow is covered by automated tests with a fake OpenAI only.
