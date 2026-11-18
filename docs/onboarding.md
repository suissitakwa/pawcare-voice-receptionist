# Adding a real business

How to set up a new business on an existing deployment, from its details to its phone number. Every command runs from `backend/` with `DATABASE_URL` pointing at the production database.

This suits a **pet groomer with one or more groomers**. The receptionist talks about dogs, and a connected Google Calendar blocks every groomer at once (each groomer's own calendar comes next). See [decision 0010](decisions/0010-multiple-businesses.md) for what comes next.

## From a sign-up

If the owner signed up and submitted a profile, start from it instead of the template:

```bash
uv run python -m app.provision list
uv run python -m app.provision draft owner@example.com > happy-tails.json
```

The draft has their name, phone, email and FAQs. Complete timezone, address, hours, groomers, sizes, services and prices with them (steps below), then:

```bash
uv run python -m app.provision create owner@example.com happy-tails.json
```

This replaces steps 2 and 3: it creates the business, links their profile, and lets them sign in to `/b/<slug>/staff` with the password they signed up with. Their profile page then shows their booking page and dashboard. It prints what's left (calendar, phone number). If they change their sign-up password later, the dashboard password doesn't change with it. Only pet grooming profiles can be provisioned for now.

## 0. Before the first real business

Once per deployment:

- [ ] `INTEGRATIONS_KEY` set on Railway (`uv run python -m app.integrations generate-key`), so each business's calendar key and webhook secret can be stored.
- [ ] Owner emails set up (`SMTP_*` and `EMAIL_FROM`, [email.md](email.md)), so owners hear about bookings and callback requests without checking the dashboard.
- [ ] The phone daily ceiling (`PHONE_DAILY_TOTAL_LIMIT`) covers the demo plus every business's expected calls.

Per business, go through [company/groomer-intake.md](../company/groomer-intake.md) with the owner. It collects everything below. Today the business must book in Google Calendar or on paper: Square, Vagaro and other software aren't connected, so they'd take the same slots as the receptionist.

## 1. Write the business file

Start from the template and fill it in with the owner:

```bash
uv run python -m app.businesses template > happy-tails.json
```

| Field | What to put |
|---|---|
| `slug` | The web address, e.g. `happy-tails-austin` → `/b/happy-tails-austin`. Lowercase letters, digits, hyphens. Can't be changed later without losing the link |
| `name`, `tagline`, `about` | What the receptionist says about the business. `about` is two or three plain sentences |
| `timezone` | The business's time zone, e.g. `America/Chicago` |
| `contact_email`, `contact_phone`, `address` | Shown on the site and given to callers who ask |
| `notify_email` | Optional. Where booking and callback emails go; without it, `contact_email` |
| `is_demo` | `false` for a real business |
| `slot_increment_minutes`, `buffer_minutes`, `min_notice_minutes`, `booking_horizon_days` | Start times every 15 minutes, 15 minutes between appointments, at least 2 hours' notice, up to 30 days ahead. Change if the owner wants |
| `hours` | Opening hours, one entry per open day. `weekday` 0 is Monday |
| `groomers` | Each groomer with working hours and breaks. Bookings fit inside these. With more than one, customers can ask for someone by name or take anyone free. Add `"services": ["full-groom"]` to limit a groomer to some services; leave it out for every service. A groomer removed from the file stops taking bookings |
| `size_categories` | Dog sizes. A size with `requires_staff_assessment: true` can't be booked directly; callers get a callback instead |
| `services` | Each service's duration and price (in cents) for each size it's offered for |
| `faqs` | The only answers the receptionist gives beyond services and hours. Keep them short and true |
| `voice_daily_limit`, `phone_daily_limit` | Optional. Browser voice sessions and phone calls this business may take per day, replacing the deployment's `VOICE_DAILY_SESSION_LIMIT` (30) and `PHONE_DAILY_CALL_LIMIT` (50). `0` turns that channel off for the business |

Check it without saving anything:

```bash
uv run python -m app.businesses check happy-tails.json
```

## 2. Create it

```bash
uv run python -m app.businesses load happy-tails.json
uv run python -m app.businesses list
```

To change prices, hours or FAQs later, edit the file and load it again with `--update`. Services left out of the file are switched off, not deleted. Bookings, staff, numbers and integrations are never touched by a reload.

## 3. Staff sign-in

```bash
uv run python -m app.staff create happy-tails-austin owner@happytails.example "Sam"
```

It asks for the password. The owner signs in at `https://YOUR-SITE.netlify.app/b/happy-tails-austin/staff`, which shows only their business's bookings, callbacks, blocked time and webhook status.

## 4. Phone number

Buy a Twilio number for the business and point it at the same OpenAI trunk or Vapi server as the demo ([phone.md](phone.md)), then:

```bash
uv run python -m app.phone_numbers add happy-tails-austin +15125550100
```

Once any number is registered, register the demo's number too (or keep `PHONE_NUMBER` set), or calls to it are declined. See [phone.md](phone.md#numbers-for-several-businesses).

## 5. Calendar and automations (optional)

With `INTEGRATIONS_KEY` set on the deployment:

```bash
uv run python -m app.integrations set-calendar happy-tails-austin owner@gmail.com key.json
uv run python -m app.integrations set-webhook happy-tails-austin https://...
```

See [calendar.md](calendar.md#several-businesses) and [automations.md](automations.md#several-businesses).

## 6. Check it

1. `https://YOUR-SITE.netlify.app/b/happy-tails-austin` shows the right name, services, prices, hours and number, with no demo notices. This is the business's booking site; the owner can link to it from their own website or Google listing.
2. Call the number. The receptionist greets with the business's name and offers its services.
3. Book a test appointment by phone, check it in the calendar (if connected), and cancel it.

## Not ready yet

- Each groomer's own Google Calendar. One calendar per business, which blocks everyone.
