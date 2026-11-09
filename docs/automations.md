# Automations with n8n

The backend sends an event to one webhook URL whenever something happens that a person should hear about. An automation tool such as [n8n](https://n8n.io) receives it and does the rest: emails, texts, calendar entries, spreadsheets.

## Events

| Event | When |
| --- | --- |
| `booking.confirmed` | A booking is confirmed, from the form or by voice. Sent once, even if the confirmation is retried |
| `booking.cancelled` | Staff cancel a booking. Sent once, even if cancel is pressed twice |
| `callback.requested` | The voice assistant takes a callback request |
| `webhook.test` | Staff press **Send test event** on the dashboard |

Each event is a JSON `POST`:

```json
{
  "id": "8f0c…",
  "type": "booking.confirmed",
  "occurred_at": "2027-01-04T16:00:00+00:00",
  "business": { "slug": "pawcare-demo", "name": "PawCare Grooming Studio", "timezone": "America/Los_Angeles" },
  "data": {
    "reference": "PC-ABCD2345",
    "status": "confirmed",
    "service": "Full Groom",
    "size": "Medium",
    "starts_at": "2027-01-05T17:00:00+00:00",
    "local_start": "2027-01-05T09:00:00-08:00",
    "local_end": "2027-01-05T11:00:00-08:00",
    "price_cents": 9000,
    "pet_name": "Biscuit",
    "owner_name": "Jordan Example",
    "owner_email": "jordan@example.com",
    "owner_phone": null,
    "source": "form"
  }
}
```

A `callback.requested` event has `data` with `id`, `name`, `phone`, `email`, `reason`, and `note`.

The payload holds customers' names and contact details. Treat the n8n workflow and its execution log as personal data.

## Delivery

- Events are written in the same database transaction as the booking or callback, so nothing is sent for a change that did not happen, and nothing is lost if the webhook is down.
- A worker in the backend sends due events every 5 seconds. Any `2xx` response counts as delivered.
- Failures are retried after 30 seconds, then 1, 2, 4 minutes and so on, up to an hour apart, 8 attempts in all. Every retry has the same `X-Webhook-Id`, so a workflow can ignore one it has already handled.
- An event not delivered within 24 hours is dropped as stale, because a day-late notification does more harm than good. Events are deleted after 30 days.
- The staff dashboard's **Automations** section shows whether the webhook is configured, what is waiting, and what was sent or failed in the last 7 days.

## Setting it up

1. In n8n, create a workflow starting with a **Webhook** node: method `POST`, a path of your choice. Turn on its raw body option, so the exact bytes are available for checking the signature. Copy the production URL.
2. Generate a secret of at least 32 characters, for example with `openssl rand -hex 32`. Store it as an n8n credential or variable, not in the workflow text.
3. In Railway, set on the backend service:

   | Variable | Value |
   | --- | --- |
   | `WEBHOOK_URL` | The n8n production webhook URL. It must use `https` |
   | `WEBHOOK_SECRET` | The secret from step 2 |

4. Deploy, open the staff dashboard, and press **Send test event**. Within a few seconds the n8n execution log shows a `webhook.test` event, and **Sent in the last 7 days** goes up by one.
5. Add the steps that matter, for example an **IF** or **Switch** node on the `X-Webhook-Event` header, then **Send Email** or **Twilio** nodes.

## Checking the signature

Every request carries:

| Header | Meaning |
| --- | --- |
| `X-Webhook-Id` | The event id, the same on every retry |
| `X-Webhook-Event` | The event type |
| `X-Webhook-Timestamp` | Unix seconds when this attempt was signed |
| `X-Webhook-Signature` | `sha256=` followed by the hex HMAC-SHA256 of `<timestamp>.<raw body>`, keyed with the secret |

Reject a request when the signature does not match, or when the timestamp is more than 5 minutes old, which stops a captured request being replayed. A **Code** node after the Webhook node can do this. The sketch below assumes the raw body is in the binary property `data` and that the Code node may use Node's `crypto` module (on self-hosted n8n, allow it with `NODE_FUNCTION_ALLOW_BUILTIN=crypto`). Check both against your n8n version.

```js
const crypto = require("crypto");
const secret = $env.WEBHOOK_SECRET; // or a credential
const headers = $json.headers;
const raw = await this.helpers.getBinaryDataBuffer(0, "data");

const timestamp = headers["x-webhook-timestamp"];
const expected = "sha256=" + crypto.createHmac("sha256", secret).update(`${timestamp}.`).update(raw).digest("hex");
const given = headers["x-webhook-signature"] ?? "";
const fresh = Math.abs(Date.now() / 1000 - Number(timestamp)) < 300;
const valid = given.length === expected.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
if (!valid || !fresh) throw new Error("Rejected webhook: bad signature or stale timestamp");

return [{ json: { event: headers["x-webhook-event"], id: headers["x-webhook-id"], ...JSON.parse(raw.toString("utf8")) } }];
```

## Not covered yet

- One webhook URL per deployment. Per-subscriber webhooks come with subscriber receptionists.
- Events for profile sign-ups and submissions, reminders, and follow-ups are built in n8n from these events or on a schedule, or added here when needed.
