# Google Calendar

The receptionist works from the owner's real Google Calendar, in both directions:

- **Busy times block booking.** Anything marked busy in the calendar (a dentist appointment, a day off) is never offered to callers or on the website.
- **Bookings appear as events.** Every booking, change and cancellation made by phone, voice, the website or staff is written to the calendar within a few seconds.

## How it works

- Busy times come from Google's free/busy API for the day being checked, cached for 60 seconds.
- Events written by the backend are marked **Free**, so a booking never blocks itself (for example when a caller moves it by half an hour).
- Each booking has one event, with an id derived from the booking, so retries never create duplicates. A cancelled booking's event is deleted.
- A background worker syncs changed bookings every 10 seconds. If Google refuses (for example the calendar isn't shared yet), it retries after 1, 2, 4 … minutes, up to once an hour, and keeps the last error on the booking.
- If the calendar is connected but can't be read, booking stops with `calendar_unavailable` (HTTP 503) rather than risk offering a time the owner is busy.
- The event title is the service, the dog and the owner. The description has the reference, size, contact and price. It's the owner's own calendar, so they see what they need to run the day.

## Setup

Access is through a Google **service account**: a robot account the owner shares the calendar with. Nobody signs in, and it can be removed at any time by unsharing.

### 1. Create the service account

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project (or pick one).
2. **APIs & Services → Library**: enable **Google Calendar API**.
3. **IAM & Admin → Service Accounts → Create service account**. Any name, for example `pawcare-calendar`. No roles are needed.
4. Open the account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads. Keep it private; never commit it.
5. Copy the account's email address (`…@….iam.gserviceaccount.com`).

### 2. Share the calendar

1. In Google Calendar, open the calendar's **Settings and sharing**.
2. Under **Share with specific people**, add the service account's email with **Make changes to events**.
3. Under **Integrate calendar**, copy the **Calendar ID**. For your main calendar it's your Gmail address.

Some Google Workspace organisations block sharing outside the organisation; a personal Gmail calendar works without extra settings.

### 3. Railway variables

On the backend service:

- `GOOGLE_CALENDAR_ID`: the calendar id
- `GOOGLE_SERVICE_ACCOUNT_JSON`: the whole contents of the key file. If the dashboard mangles multi-line values, paste it base64-encoded instead (`base64 -i key.json | pbcopy` on a Mac).

Redeploy. Upcoming bookings made before connecting are added too.

### 4. Check it

1. Add a busy event tomorrow at 10 AM in Google Calendar. Within a minute, 10 AM is no longer offered on the website.
2. Book a time. Within a few seconds the event appears in the calendar.
3. Cancel it from the staff dashboard. The event disappears.

## Owners connect their own calendar

Businesses connect their calendar themselves, in the **Your calendar** step of their profile, by sharing it with HelloEver's service account. Nobody handles a key file per business.

**Once per deployment:** create the service account (step 1 above), set `GOOGLE_SERVICE_ACCOUNT_JSON` to its key on Railway, and make sure `INTEGRATIONS_KEY` is set. Leave `GOOGLE_CALENDAR_ID` empty unless the deployment's own business (`BUSINESS_SLUG`) should use a calendar too. Without the key, the profile says the calendar will be connected during setup.

**What the owner does:**

1. Chooses **Google Calendar**. The step shows the service account's address with a copy button.
2. In Google Calendar's settings, shares their booking calendar with that address, choosing **Make changes to events**.
3. Pastes the **Calendar ID** (their Gmail address for their main calendar) and presses **Connect calendar**.

The backend checks access before saving, by adding the calendar to the service account's calendar list, which answers with the access the owner gave:

| Google says | Owner sees |
| --- | --- |
| Not found or forbidden | "We can't see that calendar yet. Share it with … choose Make changes to events" |
| Shared to see only (`reader`, `freeBusyReader`) | "The calendar is shared, but only to see it. Change … to Make changes to events" |
| `writer` or `owner` | Connected |

- **Before the business is live:** the calendar is remembered, and `provision approve` connects the new business to it. Its follow-up steps no longer mention the calendar.
- **Once live:** connecting or disconnecting takes effect at once. Upcoming bookings are written to the new calendar, and busy times in it block booking.
- Either way the admin gets the "Profile updated" email with the calendar.

### Is it still working?

Sharing can change after connecting: the owner unshares the calendar, or turns it down to "See all event details". The connection is checked live with Google, the same way as when connecting, in two places:

- **Staff dashboard, Calendar section** (`GET /api/v1/staff/calendar`): the connected calendar, or the problem with what it means for booking, the bookings waiting to sync, when one last synced, and the last sync error. It checks on every load and on **Check again**.
- **Profile, Your calendar step:** **Check connection** (`POST /api/v1/subscribers/profile/calendar/check`) says it's working or gives Google's reason. It saves and disconnects nothing.

| Check says | What happens to booking |
| --- | --- |
| Not shared (`calendar_not_shared`) | Busy times can't be read, so callers and the booking site can't book until it's shared again |
| Read only (`calendar_read_only`) | Busy times still block booking, but new bookings aren't added as events |
| Unreachable (`calendar_unreachable`) | Booking stops while Google can't be reached |
| Settings unreadable (`calendar_settings_unreadable`) | The stored connection or `GOOGLE_SERVICE_ACCOUNT_JSON` can't be used; booking is paused until it's fixed on our side |

### When it stops working

The calendar sync worker also checks every connected calendar every 15 minutes, the same way, and remembers the result on its `business_integrations` row (migration `0026`).

- When a check finds a problem, the owner is emailed "Action needed: … can't use your Google Calendar" with the reason and what it means for booking, the business's webhook gets `calendar.failing`, and `ADMIN_EMAIL` gets "Calendar failing: …". One outage sends one email, however long it lasts.
- Google being unreachable alerts only once it has lasted 30 minutes, so a short blip sends nothing. Not shared, read only and unreadable settings alert on the first check.
- When a check finds it working again after an alert, the owner gets "Your Google Calendar works again", the webhook gets `calendar.working`, and the admin is told too.
- Connecting a calendar again starts afresh: it's checked on the next round and can alert again.

Only calendars stored per business are checked this way. The deployment's own business on `GOOGLE_CALENDAR_ID` isn't; its dashboard section still checks it live.

### Changing or disconnecting

- Connecting a **different** calendar (or reconnecting after a disconnect) marks every upcoming confirmed booking for sync, so they're all written to it, not only the ones made afterwards. Saving the same calendar again changes nothing.
- Disconnecting stops new bookings, changes and cancellations going to the calendar. Events already in it stay there; the owner deletes them if they want.

The connection is stored with `account: shared` and no secret of its own, so the key lives only in the environment and rotating it there reaches every business. To connect one by hand once the owner has shared: `python -m app.integrations share-calendar <slug> <calendar-id>`. A business with its own service account still uses `set-calendar` below.

## Several businesses

The `GOOGLE_*` variables connect the deployment's own business (`BUSINESS_SLUG`). Any other business gets its own calendar, stored in the database with its key encrypted:

1. Once per deployment, generate a key with `uv run python -m app.integrations generate-key` and set it as `INTEGRATIONS_KEY` on the backend. Keep a copy somewhere safe: without it, stored keys can't be read.
2. Share that business's calendar with a service account as in steps 1 and 2 above, and download its JSON key.
3. From `backend/`, with `DATABASE_URL` pointing at the database:

   ```bash
   uv run python -m app.integrations set-calendar <business-slug> <calendar-id> key.json
   uv run python -m app.integrations show <business-slug>
   ```

   Delete the key file afterwards.

Each business's bookings go only to its own calendar, and only its own busy times block its booking times. A business with no calendar is never synced. The dashboard's Calendar section checks a business's own key the same way as a shared calendar. If a stored key can't be read (for example `INTEGRATIONS_KEY` changed), booking stops for that business with `calendar_unavailable`, the same as when Google can't be reached. `remove <business-slug> google_calendar` disconnects it.
