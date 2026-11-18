# Groomer intake

Questions to go through with a new groomer, on a call or by email, before setting them up. Each answer maps to a field in their business file (`uv run python -m app.businesses template`). Takes about 20 minutes.

Keep the filled-in copy private (for example `company/customers/<slug>.md`, which is not in git).

## 1. The business

| Question | Field |
|---|---|
| Business name, exactly as customers know it | `name` |
| One line about the business | `tagline` |
| Two or three sentences the receptionist can say about you: who you are, what makes you different | `about` |
| Address customers come to (or "mobile, we come to you" plus the area) | `address` |
| Public email and phone for customers | `contact_email`, `contact_phone` |
| City and state | `timezone`, `slug` (e.g. `happy-tails-austin`) |

## 2. When you work

| Question | Field |
|---|---|
| Opening hours, day by day | `hours` |
| The hours you actually groom, if different, and your breaks | `groomers[0].hours`, `breaks` |
| Gap you want between dogs (clean-up, drying) | `buffer_minutes` (default 15) |
| How soon can someone book? Same day? | `min_notice_minutes` (default 120) |
| How far ahead do you take bookings? | `booking_horizon_days` (default 30) |
| Do appointments start on the hour, half hour, or every 15 minutes? | `slot_increment_minutes` |

## 3. Dogs and services

| Question | Field |
|---|---|
| How do you size dogs (by weight, by breed)? Where are the cut-offs? | `size_categories` |
| Any dogs you need to see or talk about first (giant breeds, matted coats, aggressive dogs)? | `requires_staff_assessment: true` on that size |
| Every service you offer, what's included, and for each size: how long it takes and the price | `services` |
| Cats? (The receptionist only books dogs for now. Cat requests become callbacks.) | (note only) |

## 4. What callers ask

Ask for the ten questions they get most on the phone, and their answers. Typical ones:

- Do you need vaccination records? Which ones?
- What's the cancellation or no-show policy?
- Do you take matted dogs, puppies, seniors?
- How do I pay? Deposits?
- Is there parking? Do I stay with my dog?
- How long until pick-up?

Each becomes an entry in `faqs`. The receptionist answers only from these, so anything missing becomes "I'll have the groomer call you back".

## 5. Calendar and messages

| Question | What it decides |
|---|---|
| Where do you keep bookings today: Google Calendar, Square, Vagaro, MoeGo, paper? | **Google Calendar or paper: ready.** Anything else: not yet, see the note below |
| Which Google account? | Calendar to share with the service account ([calendar.md](../docs/calendar.md)) |
| How do you want to hear about new bookings and callback requests: email, text? | Webhook to n8n ([automations.md](../docs/automations.md)), or they check the staff dashboard |
| Email for the staff dashboard login | `python -m app.staff create` |

**Square, Vagaro and other booking software are not connected yet.** The receptionist would book into its own system and the groomer's software wouldn't know, so both could take the same slot. Only take such a groomer if they agree to move bookings to Google Calendar for the pilot.

## 6. The phone line

| Question | What it decides |
|---|---|
| Will callers reach the receptionist on a new number, or do you forward your existing number when you're busy? | New Twilio number; forwarding set up on their side (no-answer or busy forwarding to the Twilio number) |
| How many calls a day do you get? | `phone_daily_limit` |
| Anything the receptionist must never say or promise? | `about` / `faqs` wording |

## 7. The pilot

- Free for 30 days, no card.
- What they'll check at the end: missed calls answered, bookings made, callbacks requested.
- They can switch it off any time by removing call forwarding.
