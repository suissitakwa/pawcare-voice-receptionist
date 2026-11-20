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

- **To you**, at `ADMIN_EMAIL` (for example `takwa@helloeverai.com`): "New sign-up: <business> (<type>)", with their contact details, calendar, and the `python -m app.provision draft <email>` command to start from.
- **To them**: "We received your profile for <business>", saying you'll be in touch within one business day, with replies going to `ADMIN_EMAIL`.

Without `ADMIN_EMAIL`, only their receipt is sent. `SERVICE_NAME` (default `HelloEver AI`) names the sender and signs the receipt.

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
