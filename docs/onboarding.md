# Adding a real business

How to set up a new business on an existing deployment, from its details to its phone number. Every command runs from `backend/` with `DATABASE_URL` pointing at the production database.

For now this suits a **solo pet groomer**: each business books against one groomer's calendar, and the receptionist talks about dogs. See [decision 0010](decisions/0010-multiple-businesses.md) for what comes next.

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
| `is_demo` | `false` for a real business |
| `slot_increment_minutes`, `buffer_minutes`, `min_notice_minutes`, `booking_horizon_days` | Start times every 15 minutes, 15 minutes between appointments, at least 2 hours' notice, up to 30 days ahead. Change if the owner wants |
| `hours` | Opening hours, one entry per open day. `weekday` 0 is Monday |
| `groomers` | One groomer with working hours and breaks. Bookings fit inside these |
| `size_categories` | Dog sizes. A size with `requires_staff_assessment: true` can't be booked directly; callers get a callback instead |
| `services` | Each service's duration and price (in cents) for each size it's offered for |
| `faqs` | The only answers the receptionist gives beyond services and hours. Keep them short and true |

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

- One groomer per business.
- Browser voice limits are shared by all businesses on the deployment.
