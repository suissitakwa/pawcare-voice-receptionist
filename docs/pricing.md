# Pricing and the free trial

One plan for now: **$49 a month for up to 100 phone calls**, after a **14-day free trial**. No card is taken for the trial and nothing is charged automatically. To keep their receptionist, owners either **pay by card through Stripe** from their dashboard (when Stripe is set up), or ask for an invoice that you send and then mark paid.

The rules live in `backend/app/plans.py` (`PRICE`, `MONTHLY_CALLS`, `TRIAL_DAYS`). The sign-up page shows the same figures from `frontend/src/PlanPanel.tsx`, so change both together.

## Plans

| Plan | Who | Calls |
|---|---|---|
| `trial` | Every business made from a sign-up (`provision approve` or automatic approval). The trial starts when it goes live and lasts 14 days | Up to 100 a month |
| `starter` | Paying customers: after a Stripe checkout, or once you mark them paid | Up to 100 a month |
| `unlimited` | The demo, pilots, businesses loaded from a file, and every business from before plans | No allowance (the daily limits still apply) |

Calls are phone calls answered for the business since the 1st of the month, in its own time zone. Message-only calls count too.

## When the trial ends, card payments stop, or the calls run out

The receptionist still answers, but **only takes messages**: no times, prices or bookings, just a callback request, the same as when a day's call limit is reached. The website's voice assistant pauses ("Please use the booking form"). The booking form keeps working, since it costs nothing to run.

On the 1st, calls come back for `trial` (while it lasts) and `starter`. A trial that has ended stays message-only until the owner pays or you set a plan.

A card subscription that Stripe has **cancelled** (by the owner in the portal, or after its failed-payment retries) or marks **unpaid** also turns the receptionist message-only, until the owner pays again or you set a plan. While a payment is **past due** and Stripe is still retrying, everything keeps working and the dashboard asks the owner to update their card.

## What the owner sees

- **Sign-up page:** "Free for 14 days, then $49 a month for up to 100 calls. No card needed, and nothing is charged automatically."
- **"Set up" email** at approval: when the trial ends, the price, and how to keep it ([email.md](email.md#going-live)).
- **Staff dashboard, Your plan:**
  - days left in the trial, or that it has ended;
  - this month's calls out of 100;
  - a warning once the receptionist only takes messages;
  - with Stripe set up: **Pay by card ($49 a month)**, which opens Stripe Checkout, and after paying **Manage billing**, which opens Stripe's customer portal (card, invoices, cancelling);
  - without Stripe: **Keep my receptionist ($49 a month)**. Pressing it once emails you "Plan request: <business> wants to keep their receptionist". After that the dashboard says an invoice is coming.

  Businesses on `unlimited` don't see the section.

## Card payment through Stripe

The code is in `backend/app/billing.py`. Card details only ever go to Stripe.

1. **Checkout:** `POST /api/v1/staff/plan/checkout` creates a Checkout Session in subscription mode for `STRIPE_PRICE_ID`, tagged with the business's ID, and the dashboard goes there. Stripe sends the owner back to `PUBLIC_SITE_URL/staff?billing=paid` (or `?billing=cancelled`).
2. **Webhook:** Stripe calls `POST /api/v1/stripe/webhook`, signed with `STRIPE_WEBHOOK_SECRET`; unsigned events, and ones signed more than 5 minutes ago, get 400. Handled events:

   | Event | Effect |
   |---|---|
   | `checkout.session.completed` (paid) | Plan `starter`, trial and request cleared, Stripe customer and subscription saved |
   | `checkout.session.async_payment_succeeded` | The same, for a payment that cleared later |
   | `customer.subscription.created` / `updated` | `active`, `trialing`, `past_due`: stays on `starter`. `canceled`, `unpaid`, `incomplete_expired`: message-only |
   | `customer.subscription.deleted` | Message-only |

   Events about an older subscription than the one the business pays with are ignored, so a late cancellation doesn't stop a new subscription. Receiving an event twice is harmless. A business on `unlimited` stays unlimited even if it pays.
3. **Portal:** `POST /api/v1/staff/plan/portal` opens Stripe's customer portal for the business's customer.

### Setting it up

1. In Stripe, create a product "Receptionist" with a **$49 monthly recurring price**, and copy its ID (`price_...`).
2. In **Settings → Billing → Customer portal**, turn on updating the payment method, invoice history and cancelling.
3. In **Developers → Webhooks**, add the endpoint `https://<backend>/api/v1/stripe/webhook` with these events (`checkout.session.completed`, `checkout.session.async_payment_succeeded`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`), and copy its signing secret (`whsec_...`).
4. In Railway's backend variables, set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, and `PUBLIC_SITE_URL` (the dashboard's address). Use test-mode keys first and pay with Stripe's test card `4242 4242 4242 4242`.

If any of the four is missing, card payment is off and the dashboard falls back to asking for an invoice. Setting a plan by hand (below) always wins over a stopped card subscription.

## What you do

When a plan request arrives (only without Stripe):

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

Setting `starter` or `unlimited` ends the trial, clears the request, and overrides a stopped card subscription. `show` also prints the card subscription's status.

## API

- `GET /api/v1/staff/plan`: `plan`, `state` (`trial`, `trial_ended`, `active`, `payment_stopped`, `allowance_used` or `unlimited`), `price`, `calls_included`, `calls_this_month`, `trial_ends_at`, `upgrade_requested_at`, `billing_status` (Stripe's subscription status, or null), `card_payment` (Stripe is set up), `manage_billing` (the portal can open).
- `POST /api/v1/staff/plan/request`: records the request once and emails `ADMIN_EMAIL`. Returns the same status. A business on `unlimited` gets 409 `no_plan_needed`.
- `POST /api/v1/staff/plan/checkout`: `{"url": <Stripe Checkout>}`. 409 `card_payment_off` without Stripe, `no_plan_needed` for `unlimited`, `already_paying` with a working subscription; 502 `billing_unavailable` when Stripe fails.
- `POST /api/v1/staff/plan/portal`: `{"url": <Stripe customer portal>}`. 409 `no_billing_account` before the first card payment.
- `POST /api/v1/stripe/webhook`: for Stripe only, signed. 404 until `STRIPE_WEBHOOK_SECRET` is set.

## Not done yet

- An email to you when a business pays by card or its payments stop. For now, see Stripe's dashboard, or `python -m app.plans show <slug>`.
- Not tried against Stripe's real test mode yet: the webhook and API calls are covered by tests with Stripe's documented formats.
- Emails before the trial ends. For now the dashboard and the "set up" email carry it.
- More plans, and a cost in dollars per call to check the margin. The dashboard records tokens, not dollars, so check the OpenAI and Twilio usage pages after real calls.
