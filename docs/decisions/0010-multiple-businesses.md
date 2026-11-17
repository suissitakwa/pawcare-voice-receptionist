# 0010: Serve many businesses from one deployment

Status: accepted. PRs 1–4 done; plan re-ordered for a first pilot.

## Context

PawCare runs one receptionist for one business. To sell it to groomers, hair salons and later nail salons, one deployment must serve many businesses, each with its own site, phone number, staff, calendar and settings, and none able to see another's data.

The data model is mostly ready: every table either has a `business_id` or hangs off a row that does. What is single-business is **how a request picks its business** and **where per-business settings live**.

### Where the code assumes one business

| Place | Today | Problem |
|---|---|---|
| [main.py:173](../../backend/app/main.py) `current_business_id` | Looks up `settings.business_slug` | Every public route (business, services, FAQs, availability, booking, voice) serves the same business |
| [staff_api.py:122](../../backend/app/staff_api.py) login | Looks up `settings.business_slug` | Staff of a second business can't sign in |
| [phone_api.py:88](../../backend/app/phone_api.py), [vapi_api.py:58](../../backend/app/vapi_api.py) | Looks up `settings.business_slug` | Every phone call goes to the same business, whatever number was dialed |
| [staff.py:114](../../backend/app/staff.py) seed staff | One staff login from env vars | Can't create staff for other businesses |
| [config.py](../../backend/app/config.py) | `phone_number`, `google_calendar_id`, `google_service_account_json`, `webhook_url`, `webhook_secret`, `vapi_assistant_overrides`, voice/phone daily limits | Integrations and limits are server-wide, not per business |
| [availability.py:109](../../backend/app/availability.py) `active_groomer_id` | First active groomer only | One calendar per business. Hair salons have several stylists |
| `business_profiles` table | Not linked to `businesses` | A subscriber's submitted profile never becomes a live receptionist |

Already fine: staff sessions resolve to a staff user with a `business_id`; bookings, drafts, callbacks, voice sessions, webhooks and blocks all carry or inherit `business_id`; uniqueness constraints are per business.

## Decision

1. **The business comes from the request, never from settings.**
   - Public routes: `/api/v1/b/{slug}/...`. The current unprefixed routes stay as aliases for the demo business until the frontend moves, then are removed.
   - Staff routes: the business comes from the signed-in staff user. Login takes the business slug (`/api/v1/b/{slug}/staff/login`), because the same email can work at two businesses.
   - Phone: the **dialed number** picks the business, through a new `business_phone_numbers` table (number unique → business). OpenAI SIP: read the `To` SIP header. Vapi: read the call's phone number. A call to an unknown number is declined and logged.
2. **Per-business settings move into the database.** A new `business_integrations` table: `business_id`, `kind` (`google_calendar`, `webhook`, later `square`, `vapi`), non-secret `config` (JSONB), and `secrets` encrypted with a key from the environment (Fernet, `cryptography` is already a dependency). The env values stay only as the demo business's seed.
3. **Limits per business.** Voice and phone daily limits are counted per `business_id`, with server-wide caps kept as a safety ceiling against cost blowups.
4. **Isolation is tested, not assumed.** The test suite seeds two businesses, and every route gets a test that business B can't read or change business A's data (same reference, same block id, same callback id).
5. **A submitted profile can become a business.** `business_profiles.business_id` (nullable FK). A `manage.py provision <profile>` command creates the business, its hours, services and first staff login from the profile, for manual approval. Self-serve comes later.

Not chosen: one deployment per customer (simple but every fix ships N times, the problem we're leaving), and Postgres row-level security now (a good second layer later; app-level filtering plus tests first).

## Plan, one PR each

Done:

1. **Request-scoped business.** `/api/v1/b/{slug}/...` routes, old routes as demo aliases. Second business in test fixtures and cross-business tests for public routes.
2. **Staff per business.** Slug-scoped login; `python -m app.staff create <slug> <email>`; cross-business tests for staff routes.
3. **Phone routing.** `business_phone_numbers` table, routing by dialed number for OpenAI SIP and Vapi. An unregistered `PHONE_NUMBER`, an unreadable dialed number, or a deployment with no registered numbers still reaches `BUSINESS_SLUG`, so production keeps working with no data change.
4. **Per-business integrations.** `business_integrations` with encrypted secrets; Google Calendar and webhooks read from it; the sync and webhook workers loop over businesses. The `GOOGLE_*` and `WEBHOOK_*` settings keep serving `BUSINESS_SLUG` when it has nothing stored.

Next, re-ordered on 2026-11-17 to reach a first real pilot (one solo pet groomer) as soon as possible:

5. **Voice tools from each business's catalog.** The assistant's tool definitions listed the demo's three service slugs, so no other business's services could be booked by voice. Build them from the business's own services and sizes.
6. **Create a business from a file.** `python -m app.businesses load <file.json>`: name, hours, services, prices, FAQs, using the seed format, with `is_demo` false.
7. **Frontend per business.** Booking site at `/b/{slug}`, staff dashboard sign-in per business; demo stays at `/`. Real businesses don't show the "fictional business" notice.
8. **Per-business limits.** Count browser voice usage per business, keep a server-wide ceiling on cost.

Then a real test call to a second number and a connected second calendar, and the pilot.

After the pilot:

9. **Provisioning from subscriber profiles.** Link `business_profiles` to businesses.
10. **Several staff calendars.** Replace `active_groomer_id` with "any qualified staff member" or a requested one. Needed before multi-groomer shops and hair salons.

Each PR keeps the demo working and the evals green.

## Out of scope here

Industry packages (pet/hair/nails fields, scripts, renaming `groomer` and `pet_name`), the booking connector and Square/Vagaro, self-serve signup and billing. They build on this.

## Consequences

- Every new query must filter by business. The two-business tests are the guard.
- Phone numbers must be registered per business before a call can be answered.
- A leaked encryption key exposes every business's integration secrets, so it lives only in the deployment's secret store and can be rotated.
