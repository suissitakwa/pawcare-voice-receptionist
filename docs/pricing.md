# Pricing and the free trial

One plan for now: **$49 a month for up to 100 phone calls**, after a **14-day free trial**. No card is taken and nothing is charged automatically. Owners ask to keep their receptionist from the dashboard, you send an invoice, and you mark them paid.

The rules live in `backend/app/plans.py` (`PRICE`, `MONTHLY_CALLS`, `TRIAL_DAYS`). The sign-up page shows the same figures from `frontend/src/PlanPanel.tsx`, so change both together.

## Plans

| Plan | Who | Calls |
|---|---|---|
| `trial` | Every business made from a sign-up (`provision approve` or automatic approval). The trial starts when it goes live and lasts 14 days | Up to 100 a month |
| `starter` | Paying customers, once you mark them paid | Up to 100 a month |
| `unlimited` | The demo, pilots, businesses loaded from a file, and every business from before plans | No allowance (the daily limits still apply) |

Calls are phone calls answered for the business since the 1st of the month, in its own time zone. Message-only calls count too.

## When the trial ends, or the calls run out

The receptionist still answers, but **only takes messages**: no times, prices or bookings, just a callback request, the same as when a day's call limit is reached. The website's voice assistant pauses ("Please use the booking form"). The booking form keeps working, since it costs nothing to run.

On the 1st, calls come back for `trial` (while it lasts) and `starter`. A trial that has ended stays message-only until you set a plan.

## What the owner sees

- **Sign-up page:** "Free for 14 days, then $49 a month for up to 100 calls. No card needed, and nothing is charged automatically."
- **"Set up" email** at approval: when the trial ends, the price, and how to keep it ([email.md](email.md#going-live)).
- **Staff dashboard, Your plan:**
  - days left in the trial, or that it has ended;
  - this month's calls out of 100;
  - a warning once the receptionist only takes messages;
  - **Keep my receptionist ($49 a month)**.

  Pressing it once emails you "Plan request: <business> wants to keep their receptionist". After that the dashboard says an invoice is coming. Businesses on `unlimited` don't see the section.

## What you do

When a plan request arrives:

1. Send an invoice to the business's contact email (in the request email).
2. Once it's paid, in Railway's backend Console:

   ```bash
   /opt/venv/bin/python -m app.plans set <slug> starter
   ```

Other commands:

```bash
/opt/venv/bin/python -m app.plans show <slug>               # plan, trial end, calls this month
/opt/venv/bin/python -m app.plans set <slug> trial 30       # restart or extend a trial
/opt/venv/bin/python -m app.plans set <slug> unlimited      # a pilot or a friend
```

Setting `starter` or `unlimited` ends the trial and clears the request.

## API

- `GET /api/v1/staff/plan`: `plan`, `state` (`trial`, `trial_ended`, `active`, `allowance_used` or `unlimited`), `price`, `calls_included`, `calls_this_month`, `trial_ends_at`, `upgrade_requested_at`.
- `POST /api/v1/staff/plan/request`: records the request once and emails `ADMIN_EMAIL`. Returns the same status. A business on `unlimited` gets 409 `no_plan_needed`.

## Not done yet

- Payment through Stripe (a payment page and customer portal). Invoices are by hand for now.
- Emails before the trial ends. For now the dashboard and the "set up" email carry it.
- More plans, and a cost in dollars per call to check the margin. The dashboard records tokens, not dollars, so check the OpenAI and Twilio usage pages after real calls.
