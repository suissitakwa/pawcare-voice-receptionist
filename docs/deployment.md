# Deployment

| Part | Host | Config |
|---|---|---|
| Frontend | Netlify | [netlify.toml](../netlify.toml) |
| Backend | Railway, built from the Dockerfile | [backend/railway.json](../backend/railway.json) |
| Database | Railway PostgreSQL | Service variables |

## First deployment

### 1. Database

In a new Railway project on a paid plan or active trial, add a **PostgreSQL** service.

### 2. Backend

Add a service from the GitHub repository, then set these under its Settings:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Railway Config File | `/backend/railway.json` (this path does not follow Root Directory, so it must be the full path) |

Set these under Variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `ENVIRONMENT` | `production` |
| `BUSINESS_SLUG` | `pawcare-demo` |
| `SEED_DEMO_DATA` | `true` |
| `CORS_ALLOWED_ORIGINS` | Leave as a placeholder until step 3 |

Under Networking, generate a public domain, and then check the deployment:

```bash
curl -s https://YOUR-BACKEND.up.railway.app/health/ready
```

Expect `"status":"ready"`. If migration 0001 fails on `btree_gist`, stop here: the booking design in [decision 0002](decisions/0002-booking-consistency.md) depends on that extension.

### 3. Frontend

In Netlify, import the same repository. `netlify.toml` supplies the build settings. Set `VITE_API_BASE_URL` to the Railway backend's public URL, with no trailing slash. This value is compiled into the bundle, so changing it requires a rebuild.

### 4. Connect them

Set `CORS_ALLOWED_ORIGINS` on the backend to the exact Netlify origin, for example `https://your-site.netlify.app`. No wildcards and no trailing slash. Click **Deploy** to apply it, and wait for **Active**: the old deployment keeps serving the old value until the rollout finishes.

### 5. Verify

```bash
curl -s https://YOUR-BACKEND.up.railway.app/api/v1/services
```

Then open the Netlify URL and confirm that services, hours, and FAQs appear, with no CORS error in the browser console.

## How a deploy behaves

- The container runs `alembic upgrade head`, then the seed if `SEED_DEMO_DATA=true`, then the server.
- Railway waits for `/health/ready`, which passes only when the database is reachable and the schema matches this build. A failed check keeps the previous deployment serving.
- The seed overwrites business details, services, hours, and FAQs with the contents of `demo_business.json`. Set `SEED_DEMO_DATA=false` before staff need to edit these records in production.
- Only changes under `backend/` trigger a backend deploy.

## Cost control

Set a usage limit in Railway's workspace settings before sharing the URL. Voice costs are separate. See the voice section below.

## Staff account

The dashboard is at `/staff` on the website. To create the staff account:

1. On your own computer, in `backend/`, run `uv run python -m app.staff hash`, enter a password of at least 12 characters, and copy the printed hash. It starts with `$argon2id$`.
2. In Railway, set these variables on the backend service, then deploy:

   | Variable | Value |
   |---|---|
   | `STAFF_EMAIL` | The staff sign-in email |
   | `STAFF_DISPLAY_NAME` | The name shown on the dashboard |
   | `STAFF_PASSWORD_HASH` | The hash from step 1 |

3. Sign in at `https://YOUR-SITE.netlify.app/staff`.

The container creates or updates the account on every start. To change the password, generate a new hash and replace the variable. The password itself is never stored in Railway.

## Voice

Voice runs in one of three modes, set by `VOICE_MODE` on the backend:

| Mode | Behaviour |
|---|---|
| `off` | The kill switch. "Start talking" explains that voice is turned off |
| `test` | A clearly labelled scripted preview. No microphone, no provider, no cost. This is the default |
| `live` | Real voice through OpenAI's Realtime API |

To go live:

1. Create an API key at https://platform.openai.com/api-keys, and set a monthly spending limit in OpenAI's billing settings first.
2. In Railway, set these on the backend service, then deploy:

   | Variable | Value |
   |---|---|
   | `OPENAI_API_KEY` | The key. It stays on the server and is never sent to the browser |
   | `VOICE_MODE` | `live` |
   | `VOICE_DAILY_SESSION_LIMIT` | Optional, default `30` |
   | `VOICE_SESSIONS_PER_CLIENT_PER_HOUR` | Optional, default `6` |
   | `VOICE_MAX_SESSION_SECONDS` | Optional, default `300` |
   | `VOICE_MODEL` | Optional, default `gpt-realtime-2.1-mini` |

3. Open the live site, click "Start talking", and allow the microphone.

To stop all voice spending at once, set `VOICE_MODE=off` and deploy. Calls already in progress continue until they end or reach their time limit.

The browser enforces the session time limit. A visitor who modifies the page could keep a call open longer, so the daily quota, the per-visitor limit, and the OpenAI spending limit are what cap cost.

## Automation webhook (optional)

Bookings, cancellations, and callback requests can be sent to an n8n workflow or any other webhook. Set `WEBHOOK_URL` (https) and `WEBHOOK_SECRET` (at least 32 characters) on the backend service, deploy, then press **Send test event** in the staff dashboard. Without them, events are kept for 30 days and not sent. Full guide: [automations.md](automations.md).

### Phone line

To answer a phone number, set `OPENAI_WEBHOOK_SECRET` (from the OpenAI project's webhook for `realtime.call.incoming`) and `PHONE_ENABLED=true`, with `VOICE_MODE=live`. Set `PHONE_NUMBER` (E.164) to show a call button on the website. `PHONE_VOICE`, `PHONE_MODEL` and `PHONE_VAD_EAGERNESS` (default `low`) tune calls without changing the website assistant. Then point a Twilio Elastic SIP Trunk at `sip:<project id>@sip.api.openai.com;transport=tls`. Limits: `PHONE_MAX_CALL_SECONDS` (600), `PHONE_DAILY_CALL_LIMIT` (50), `PHONE_CALLS_PER_CALLER_PER_HOUR` (4). Full guide: [phone.md](phone.md).
