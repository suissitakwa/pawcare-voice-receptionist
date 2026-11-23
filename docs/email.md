# Owner emails

The owner of each business gets an email for every new, changed or cancelled booking and every callback request, so nothing waits in the dashboard unseen. No automation tool is needed.

## How it works

- Emails come from the same outbox as the [webhook](automations.md): one is only sent for a change that was saved, and a mail server being down only delays it.
- Each event is emailed once. A failed send is retried after 1, 2, 4 … minutes, at most 6 times, and given up after 24 hours: an old notice does more harm than good.
- It goes to the business's `notify_email`, or its `contact_email` if none is set. The demo business never emails unless `notify_email` is set for it.
- The email has the time, service, dog, owner's contact, staff member, price and reference. Callback emails have the caller's phone, email, reason and note.
- The staff dashboard shows where emails go and how many were sent in the last 7 days.

## Setup

Emails go through **Resend's web API** or any **SMTP server**. On Railway use Resend: Railway blocks outgoing SMTP (ports 465 and 587) below the Pro plan, which shows up as `Network is unreachable` in the dashboard's Email notices.

### Resend (recommended)

1. Sign up at https://resend.com (free up to 3,000 emails a month) and create an API key under **API Keys** (sending access is enough).
2. On the backend service in Railway, set and redeploy:

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | the key, starting `re_` |
| `EMAIL_FROM` | for a pilot `Receptionist <onboarding@resend.dev>`; with your own domain `Receptionist <bookings@yourdomain.com>` |
| `PUBLIC_SITE_URL` | `https://YOUR-SITE.netlify.app`, for the dashboard link (optional) |

With a key set, the `SMTP_*` variables are ignored, so they can stay or go.

**Until you verify a domain, Resend only delivers to the email address you signed up to Resend with**, and only from `onboarding@resend.dev`. That's enough to test with your own address. Before emailing real owners, buy a domain, add it under **Domains** in Resend, add the DNS records it shows, and change `EMAIL_FROM` to an address on it. Emails from your own domain also land in inboxes more reliably.

### SMTP (Railway Pro or another host)

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) or `465` (TLS) |
| `SMTP_USERNAME` | the sign-in for the mail server |
| `SMTP_PASSWORD` | its password; for Gmail, an app password from https://myaccount.google.com/apppasswords (needs 2-Step Verification), without spaces |
| `EMAIL_FROM` | `Receptionist <you@gmail.com>` |

Without `RESEND_API_KEY` or `SMTP_HOST`, or without `EMAIL_FROM`, no emails are sent, and the dashboard says so.

## Sign-ups

When someone submits their profile for the first time, two emails go out:

- With `AUTO_APPROVE_SIGNUPS` on, the "New sign-up" email says it was approved automatically instead of giving the approve command.
- **To you**, at `ADMIN_EMAIL` (for example `takwa@helloeverai.com`): "New sign-up: <business> (<type>)", with their contact details, calendar, and the `python -m app.provision draft <email>` command to start from.
- **To them**: "We received your profile for <business>", saying you'll be in touch within one business day, with replies going to `ADMIN_EMAIL`.

After they've submitted, any change to their profile (business name, phone, calendar, web pages, answers, voice, documents) is emailed to `ADMIN_EMAIL` as **"Profile updated: <business>"**, with the new value of each changed section. Edits are gathered: the email goes out once they've stopped editing for 10 minutes. If their receptionist is already live, it reminds you that the live receptionist doesn't change by itself.

Without `ADMIN_EMAIL`, only their receipt is sent. `SERVICE_NAME` (default `HelloEver AI`) names the sender and signs the receipt.

## Going live

Owners who signed up hear twice, so each email says only what's true when it's sent. Replies go to `ADMIN_EMAIL`.

- **When you approve them** (`provision approve` or `create`): **"<business> is set up with HelloEver AI"**. It gives their booking page and staff dashboard, says to sign in with their sign-up email and password, names their connected Google Calendar (or says how to connect one), and says their phone number is coming.
- **When their first number is registered** (`phone_numbers add`): **"Your receptionist answers at <number>"**. It says what the receptionist does, asks them to make a test call and booking and cancel it from the dashboard, and explains how to forward their existing number (all calls, or only unanswered and busy ones) through their phone provider.

Links use `PUBLIC_SITE_URL`; set it, or the emails show only the `/b/<slug>` path. Businesses loaded from a file (no sign-up) and the demo get neither email.

## Calendar problems

When a business's connected Google Calendar stops working, the owner gets **"Action needed: <business> can't use your Google Calendar"**, with Google's reason, what it means for booking, and the dashboard link. `ADMIN_EMAIL` gets **"Calendar failing: <business> (<code>)"** with the `integrations show` command. Both get one more email when it works again. See [calendar.md](calendar.md#when-it-stops-working).

## Forgotten passwords

Both sign-in pages (`/get-started` for sign-up accounts, `/b/<slug>/staff` for dashboards) have **Forgot password?**. Whatever email is entered, the page says the same thing. If an account exists, it gets "Reset your HelloEver AI password" with a link to `PUBLIC_SITE_URL` that works once, for one hour; asking again cancels the previous link. Setting a new password signs out every other session and emails "Your password was changed". Each account can ask 3 times an hour, each visitor 10 times. The link is erased from the outbox as soon as it's emailed, and only its hash is kept. Without `PUBLIC_SITE_URL`, no link can be made, so none is sent.

## Per business

The address comes from the business file (`notify_email`, optional) or, for a business made from a sign-up, the owner's sign-up email. To change it:

```bash
uv run python -m app.businesses notify happy-tails-austin owner@happytails.example
uv run python -m app.businesses notify happy-tails-austin -    # back to contact_email
```

Or on the live backend, from Railway's web **Console** tab (no local setup needed). Use the app's own Python there:

```bash
/opt/venv/bin/python -m app.businesses notify happy-tails-austin owner@happytails.example
```

## Check it

Book a test appointment on the business's site. Within about 10 seconds the owner should get "New booking: …". If not, check the dashboard's Email notices section and the backend logs for `pawcare.email`.
