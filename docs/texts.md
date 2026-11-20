# Customer texts

Customers get a text when their booking is made, changed or cancelled, and a reminder the day before. It works for every way of booking: phone, browser voice, the booking form, and staff cancelling in the dashboard. Texts are off until switched on for a business.

## What customers get

| When | Text |
|---|---|
| Booked | `PawCare Grooming Studio: you're booked. Full Groom for Biscuit, Tue, Jan 5 at 9 AM. Ref PC-ABC123. To change it, call (323) 555-0142. Reply STOP to opt out.` |
| Changed (moved, new service, corrected name) | `…: your booking is updated. …` with the new details |
| Cancelled (by the caller or staff) | `…: your booking is cancelled. … To book again, call …` |
| Reminder, 24 hours before | `Reminder from …: Full Groom for Biscuit, Tue, Jan 5 at 9 AM. …` |

Texts name the staff member at a business with several, and the address at a mobile business. Salons and barbershops leave out the pet. Every text names the business and says how to opt out.

## How it works

- Booking texts come from the same outbox as the [webhook](automations.md) and [owner emails](email.md), so a text is only sent for a change that was saved.
- Reminders come from the bookings. Each booking remembers the start time its reminder was for, so a moved booking gets a new reminder and the same time never gets two.
- No reminder for a booking made (or moved) less than 24 hours ahead, because its confirmation just said the same thing. No reminder under an hour before the appointment, and none for cancelled bookings.
- Only bookings with a phone number get texts. Numbers without a country code are taken as US numbers.
- **At most once.** Twilio has no way to recognise a repeated request, so a request that timed out (and may have been delivered) is never sent again. When Twilio says it didn't send (down, or too many requests), the text is retried after 1, 2, 4 minutes, at most 4 times. A booking text more than 6 hours old is dropped.
- **Refusals are final.** A customer who replied STOP is blocked by Twilio itself (error 21610). That text is marked failed and not retried.
- The reasons for unsent texts are kept on the event (`text_error`) and the booking (`reminder_error`), for example `texts are off for this business` or `no phone number that can be texted`.

## Setup

### 1. Twilio credentials

On the backend service in Railway:

| Variable | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | Account SID from the Twilio console, starting `AC` |
| `TWILIO_AUTH_TOKEN` | Auth token from the same page |
| `TWILIO_MESSAGING_SERVICE_SID` | Optional. A Messaging Service (`MG…`) to send every business's texts through. Empty: each business texts from its own number |
| `REMINDER_HOURS_BEFORE` | Optional, default `24` |

Without the SID and token, nothing is texted and the worker logs `Customer texts are off`.

### 2. Register the number for texting (US)

**US carriers block texts from unregistered 10-digit numbers.** Before texting real customers, register in Twilio under **Messaging → Regulatory Compliance**:

- **A2P 10DLC** for the local numbers businesses already use for calls: a brand (your company) and a campaign of type *Customer Care* or *Low Volume Mixed*, describing appointment confirmations and reminders, with sample texts from the table above. Approval takes days to a few weeks.
- Or a **toll-free number with toll-free verification**, often faster for a pilot. Put it in a Messaging Service and set `TWILIO_MESSAGING_SERVICE_SID`.

Until a number is registered, Twilio accepts the request and the carrier drops the text (error 30034 in Twilio's message logs). The backend can't see that.

### 3. Switch texts on for a business

```bash
python -m app.businesses texts <slug> on
```

On Railway, run it in the backend's Console with `/opt/venv/bin/python`. Texts are sent from the business's first number in `python -m app.phone_numbers list`, or `PHONE_NUMBER` for the deployment's own business. A business with no number (and no Messaging Service) gets nothing, with the reason recorded. `off` stops them again.

Bookings already in the database when texts are switched on still get their reminder, as long as they have a phone number.

## What the booking form says

While a business really texts (switched on, Twilio set, and a number to text from), the public profile has `texts_customers: true`. The booking form then shows, under the phone field:

> We'll text your confirmation and a reminder the day before. Message and data rates may apply. Reply STOP to opt out.

After booking with a phone number, the confirmation screen says the details are being texted to that number. Otherwise neither appears, so the form never promises a text that won't come.

## What callers hear

Under the same condition, and only when the booking has a number that can be texted:

- **Phone calls** (OpenAI and Vapi): after booking, changing or cancelling, the tool result tells the assistant to say a text is on its way. Booked: *"…a text with the details is on its way to the number they gave, with a reminder the day before."* Changed: *"…a text with the new details is on its way."* Cancelled: *"…a text confirming the cancellation is on its way."*
- **Browser voice**: after the visitor presses Confirm booking, the assistant is told to say the same, and the on-screen confirmation says the details are being texted to that number.

## Not done yet

- The staff dashboard doesn't show texts sent or failed.
- Owners can't switch texts on themselves.
- Replies other than STOP/START/HELP (which Twilio answers itself) aren't read.
