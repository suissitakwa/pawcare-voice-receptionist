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

Redeploy. The logs show `Google Calendar sync is on`. Upcoming bookings made before connecting are added too.

### 4. Check it

1. Add a busy event tomorrow at 10 AM in Google Calendar. Within a minute, 10 AM is no longer offered on the website.
2. Book a time. Within a few seconds the event appears in the calendar.
3. Cancel it from the staff dashboard. The event disappears.
