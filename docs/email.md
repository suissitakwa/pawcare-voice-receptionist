# Owner emails

The owner of each business gets an email for every new, changed or cancelled booking and every callback request, so nothing waits in the dashboard unseen. No automation tool is needed.

## How it works

- Emails come from the same outbox as the [webhook](automations.md): one is only sent for a change that was saved, and a mail server being down only delays it.
- Each event is emailed once. A failed send is retried after 1, 2, 4 … minutes, at most 6 times, and given up after 24 hours: an old notice does more harm than good.
- It goes to the business's `notify_email`, or its `contact_email` if none is set. The demo business never emails unless `notify_email` is set for it.
- The email has the time, service, dog, owner's contact, staff member, price and reference. Callback emails have the caller's phone, email, reason and note.
- The staff dashboard shows where emails go and how many were sent in the last 7 days.

## Setup

Set these on the backend service in Railway, then redeploy:

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) or `465` (TLS) |
| `SMTP_USERNAME` | the sign-in for the mail server |
| `SMTP_PASSWORD` | its password or API key |
| `EMAIL_FROM` | `Receptionist <you@gmail.com>` |
| `PUBLIC_SITE_URL` | `https://YOUR-SITE.netlify.app`, for the dashboard link (optional) |

Without `SMTP_HOST` and `EMAIL_FROM`, no emails are sent, and the dashboard says so.

### For a pilot: Gmail

1. Turn on 2-Step Verification for the Google account.
2. Create an app password at https://myaccount.google.com/apppasswords.
3. `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME` and `EMAIL_FROM` your Gmail address, `SMTP_PASSWORD` the app password.

Gmail allows about 500 emails a day, plenty for a few businesses.

### Later: Resend (or SendGrid, Postmark)

1. Add and verify your own domain in Resend, so emails come from it and don't land in spam.
2. Create an API key.
3. `SMTP_HOST=smtp.resend.com`, `SMTP_PORT=465`, `SMTP_USERNAME=resend`, `SMTP_PASSWORD` the API key, `EMAIL_FROM=Receptionist <bookings@yourdomain.com>`.

## Per business

The address comes from the business file (`notify_email`, optional) or, for a business made from a sign-up, the owner's sign-up email. To change it:

```bash
uv run python -m app.businesses notify happy-tails-austin owner@happytails.example
uv run python -m app.businesses notify happy-tails-austin -    # back to contact_email
```

## Check it

Book a test appointment on the business's site. Within about 10 seconds the owner should get "New booking: …". If not, check the dashboard's Email notices section and the backend logs for `pawcare.email`.
