# Adding a real business

How to set up a new business on an existing deployment, from its details to its phone number. Every command runs from `backend/` with `DATABASE_URL` pointing at the production database.

This suits a **pet groomer, hair salon or barbershop** with one or more staff. Set `"industry"` in the business file (`pet_grooming`, `hair_salon` or `barbershop`; pet grooming if left out). A connected Google Calendar blocks every staff member at once (each person's own calendar comes next). See [decision 0010](decisions/0010-multiple-businesses.md) for what comes next.

## From a sign-up

When an owner signs up and submits their profile, you get a "New sign-up" email. The profile now has everything a business needs: name, phone, time zone, address, opening hours, services with length and price for each dog size, the team and who does which service, calendar, voice, and answers to common questions.

Check it with them (a short call), then in Railway's backend **Console**:

```bash
/opt/venv/bin/python -m app.provision list
/opt/venv/bin/python -m app.provision approve owner@example.com
```

`approve` prints what it will create (address, hours, every service and price, the team) and asks before creating it. It links the profile, and the owner signs in to `/b/<slug>/staff` with their sign-up password. It also keeps the voice they chose, and emails them **"<business> is set up with HelloEver AI"**: their booking page, dashboard and how to sign in, their calendar, and that their phone number is coming ([email.md](email.md#going-live)). It prints what's left (calendar, phone number). Everyone on the team works the opening hours for now; breaks and per-person hours are set in the business file afterwards.

A profile submitted before hours and prices were asked for can't be approved. Either the owner fills them in (they get a "Profile updated" email to you), or use `draft` to get the business file, fill it in, and `create` it:

```bash
/opt/venv/bin/python -m app.provision draft owner@example.com > /tmp/their-business.json
/opt/venv/bin/python -m app.provision create owner@example.com /tmp/their-business.json
```

Hours, services, prices and team in a profile may have come from the owner's website: the sign-up steps offer what the website says, and the owner chose to use it and could change it ([website-details.md](website-details.md)). Read prices in `approve`'s summary as carefully as typed ones.

**What the receptionist knows** is the profile's answers to common questions, plus services, prices and hours. Its web pages and documents are never read on calls. Owners can turn them into answers with **Suggest answers** in the profile: every suggestion is tied to words from the page or document, and only the ones they add are kept ([knowledge.md](knowledge.md)). Before approving, read the answers in `draft`. If a page they listed obviously covers something callers ask (prices, cancellations, deposits) and no answer covers it, ask them to add one.

Pet groomers, hair salons and barbershops can all be approved. The business type in the profile sets the receptionist's wording: dog sizes and pet names for groomers; for salons and barbershops, one price per service, no pet, and "client" instead of "owner".

## Plans and the free trial

Every business made from a sign-up starts a **14-day free trial** when it goes live, then costs **$49 a month for up to 100 calls**. Owners ask to keep it from their dashboard; you invoice them and run `python -m app.plans set <slug> starter`. Businesses loaded from a file, the demo and pilots are `unlimited`. See [pricing.md](pricing.md).

## Trying onboarding end to end (automatic approval)

While you're testing, set `AUTO_APPROVE_SIGNUPS=true` on Railway. Every profile then goes live the moment it's submitted, exactly as `provision approve --yes` would:

1. Sign up at `/get-started` and fill in the profile. As the business phone number, give the demo line **+1 (269) 699-2551**.
2. Submit. The profile shows the booking page and dashboard straight away. The sign-up's number is registered to ring the new business, and you get the "New sign-up" email saying it's live and that +12696992551 rings it.
3. The owner gets both go-live emails, set up and then "Your receptionist answers at (269) 699-2551" ([email.md](email.md#going-live)), instead of the "we'll be in touch" receipt.

Automatic approval only happens on a profile's first submission, and only while the setting is on. A profile submitted before that waits; approve it by hand.
4. Call +1 (269) 699-2551: the newest test business answers.

The line rings one business at a time, so **the newest sign-up with it takes it**, from the demo or from an earlier sign-up. Earlier test businesses keep their booking site and dashboard. A number you registered for a business with `phone_numbers add` is never taken; that sign-up goes live without a number. A sign-up number without an area code is skipped. A profile that can't be approved (no hours or services) stays waiting, as usual.

To give the line back to the PawCare demo: `python -m app.phone_numbers remove +12696992551`. Calls to `PHONE_NUMBER` then reach the demo again.

**Turn it off before real customers sign up.** A real owner's own number isn't on our phone line, so registering it doesn't make their calls reach the receptionist, yet the email would say it does. Next, each new customer gets their own Twilio number, linked to the business number they gave at sign-up, which they forward to it.

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
| `is_mobile`, `service_zip_codes`, `service_area` | Mobile groomers only (`industry` pet grooming). They go to the customer: every booking needs the customer's address, and its ZIP code must be in `service_zip_codes` (leave the list empty to accept any address). `service_area` is how the receptionist describes the area, e.g. "San Jose and Campbell". Salons, barbershops and salon-based groomers never ask for an address |
| `is_demo` | `false` for a real business |
| `slot_increment_minutes`, `buffer_minutes`, `min_notice_minutes`, `booking_horizon_days` | Start times every 15 minutes, 15 minutes between appointments, at least 2 hours' notice, up to 30 days ahead. Change if the owner wants |
| `hours` | Opening hours, one entry per open day. `weekday` 0 is Monday |
| `groomers` | Each groomer with working hours and breaks. Bookings fit inside these. With more than one, customers can ask for someone by name or take anyone free. Add `"services": ["full-groom"]` to limit a groomer to some services; leave it out for every service. A groomer removed from the file stops taking bookings |
| `size_categories` | Dog sizes. A size with `requires_staff_assessment: true` can't be booked directly; callers get a callback instead |
| `services` | Each service's duration and price (in cents) for each size it's offered for |
| `faqs` | The only answers the receptionist gives beyond services and hours. Keep them short and true |
| `voice` | Optional. `marin` (female) or `cedar` (male), used on the website and by phone. Leave out for the deployment's `VOICE_NAME` / `PHONE_VOICE`. With Vapi, `cedar` uses `VAPI_MALE_VOICE_ID` |
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

For a business made from a sign-up, its first number emails the owner **"Your receptionist answers at <number>"**, with a test call to make and how to forward their own number to it. Buy and set up the number in Twilio first: the email goes out as soon as it's registered.

Once any number is registered, register the demo's number too (or keep `PHONE_NUMBER` set), or calls to it are declined. See [phone.md](phone.md#numbers-for-several-businesses).

Then set the number's fallbacks in Vapi and Twilio ([fallback.md](fallback.md)), so callers reach voicemail or another phone if the receptionist is ever down.

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
4. If a calendar is connected, the staff dashboard's **Calendar** section shows it connected with nothing waiting to sync. A problem there (for example the calendar was unshared) stops booking; see [calendar.md](calendar.md#is-it-still-working). The owner and `ADMIN_EMAIL` are also emailed within 15 minutes when that happens.

## Not ready yet

- Each groomer's own Google Calendar. One calendar per business, which blocks everyone.
