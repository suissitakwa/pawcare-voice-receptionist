# Pricing and the free trial

Three monthly plans after a **14-day free trial**:

| Plan | Price | Calls a month | Per call |
|---|---|---|---|
| **Starter** | $49 | 100 | $0.49 |
| **Growth** | $99 | 300 | $0.33 |
| **Pro** | $149 | 500 | $0.30 |

No card is taken for the trial and nothing is charged automatically. To keep their receptionist, owners either **choose a plan and pay by card through Stripe** from their dashboard (when Stripe is set up), or ask for an invoice that you send and then set the plan. The cost of a call hasn't been measured in dollars yet: check the OpenAI, Vapi and Twilio usage pages after real calls before relying on these margins.

The rules live in `backend/app/plans.py` (`PAID`, `TRIAL_DAYS`, `TRIAL_CALLS`). The sign-up page shows the same figures from `PLANS` in `frontend/src/PlanPanel.tsx`, so change both together.

## Plans

| Plan | Who | Calls |
|---|---|---|
| `trial` | Every business made from a sign-up (`provision approve` or automatic approval). The trial starts when it goes live and lasts 14 days | Up to 100 a month |
| `starter`, `growth`, `pro` | Paying customers: after a Stripe checkout, or once you set the plan | 100, 300 or 500 a month |
| `unlimited` | The demo, pilots, businesses loaded from a file, and every business from before plans | No allowance (the daily limits still apply) |

Calls are phone calls answered for the business since the 1st of the month, in its own time zone. Message-only calls count too.

## When the trial ends, card payments stop, or the calls run out

The receptionist still answers, but **only takes messages**: no times, prices or bookings, just a callback request, the same as when a day's call limit is reached. The website's voice assistant pauses ("Please use the booking form"). The booking form keeps working, since it costs nothing to run.

On the 1st, calls come back for `trial` (while it lasts) and the paid plans. A trial that has ended stays message-only until the owner pays or you set a plan.

When an owner cancels in Stripe's portal, the plan keeps working until the end of the period already paid for, and the dashboard says **"Cancels on <date>"** until then; choosing "Don't cancel subscription" in the portal removes it. A card subscription that Stripe has **cancelled** (by the owner in the portal, or after its failed-payment retries) or marks **unpaid** also turns the receptionist message-only, until the owner pays again or you set a plan. While a payment is **past due** and Stripe is still retrying, everything keeps working and the dashboard asks the owner to update their card.

## What the owner sees

- **Sign-up page:** "Free for 14 days, then choose a plan: Starter $49 a month for 100 calls, Growth $99 a month for 300 calls, Pro $149 a month for 500 calls. No card needed, and nothing is charged automatically."
- **"Set up" email** at approval: when the trial ends, the three plans, and that they choose one on their dashboard ([email.md](email.md#going-live)).
- **Staff dashboard, Your plan:**
  - days left in the trial, or that it has ended;
  - the plan's name and price, and this month's calls out of its allowance;
  - a warning once the receptionist only takes messages;
  - with Stripe set up: a button for each plan (**Starter: $49 a month for 100 calls**, **Growth**, **Pro**), which opens Stripe Checkout for that plan, and after paying **Manage billing**, which opens Stripe's customer portal (switching plan, card, invoices, cancelling). A plan whose Stripe price isn't set isn't offered;
  - without Stripe: **Keep my receptionist ($49 a month)**, for Starter. Pressing it once emails you "Plan request: <business> wants to keep their receptionist". After that the dashboard says an invoice is coming.

  Businesses on `unlimited` don't see the section.

## Card payment through Stripe

The code is in `backend/app/billing.py`. Card details only ever go to Stripe.

1. **Checkout:** `POST /api/v1/staff/plan/checkout` with `{"plan": "starter" | "growth" | "pro"}` (Starter when left out) creates a Checkout Session in subscription mode for that plan's price, tagged with the business's ID and the plan, and the dashboard goes there. Stripe sends the owner back to their own dashboard, `PUBLIC_SITE_URL/b/<slug>/staff?billing=paid` (or `?billing=cancelled`); the deployment's own business uses `/staff`. The customer portal returns there too.
2. **Webhook:** Stripe calls `POST /api/v1/stripe/webhook`, signed with `STRIPE_WEBHOOK_SECRET`; unsigned events, and ones signed more than 5 minutes ago, get 400. Handled events:

   | Event | Effect |
   |---|---|
   | `checkout.session.completed` (paid) | The plan its price stands for, trial and request cleared, Stripe customer and subscription saved |
   | `checkout.session.async_payment_succeeded` | The same, for a payment that cleared later |
   | `customer.subscription.created` / `updated` | `active`, `trialing`, `past_due`: the plan its price stands for, so switching plan in the portal moves the business too. `canceled`, `unpaid`, `incomplete_expired`: message-only |
   | `customer.subscription.updated` with `cancel_at` (or `cancel_at_period_end`) | Still works; records when it ends |
| `customer.subscription.deleted` | Message-only |

   Stripe doesn't deliver events in order, so a subscription event is only a sign that something changed: the backend asks Stripe for the subscription as it is now (`GET /v1/subscriptions/<id>`) and applies that. A paid checkout does the same. An event that arrives twice, late or out of order leaves the same result. If Stripe can't be asked, the webhook answers 503 and Stripe retries it. Events about an older subscription than the one the business pays with are ignored, so a late cancellation doesn't stop a new subscription. A business on `unlimited` stays unlimited even if it pays.
3. **Portal:** `POST /api/v1/staff/plan/portal` opens Stripe's customer portal for the business's customer.

### Setting it up

1. In Stripe, create a product for each plan with its **monthly recurring price** ($49, $99, $149), and copy each price's ID (`price_...`).
2. In **Settings → Billing → Customer portal**, turn on updating the payment method, invoice history and cancelling, and under **Subscriptions → Customers can switch plans** add the three products' prices.
3. In **Developers → Webhooks**, add the endpoint `https://<backend>/api/v1/stripe/webhook` with these events (`checkout.session.completed`, `checkout.session.async_payment_succeeded`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`), and copy its signing secret (`whsec_...`).
4. In Railway's backend variables, set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` (Starter), `STRIPE_GROWTH_PRICE_ID`, `STRIPE_PRO_PRICE_ID`, and `PUBLIC_SITE_URL` (the dashboard's address). Use test-mode keys first and pay with Stripe's test card `4242 4242 4242 4242`.

If the key, the webhook secret, the Starter price or `PUBLIC_SITE_URL` is missing, card payment is off and the dashboard falls back to asking for an invoice. Setting a plan by hand (below) always wins over a stopped card subscription.

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

`set <slug> growth` and `set <slug> pro` work the same way. Setting a paid plan or `unlimited` ends the trial, clears the request, and overrides a stopped card subscription. `show` also prints the card subscription's status.

## API

- `GET /api/v1/staff/plan`: `plan`, `state` (`trial`, `trial_ended`, `active`, `payment_stopped`, `allowance_used` or `unlimited`), `price`, `calls_included`, `calls_this_month`, `trial_ends_at`, `upgrade_requested_at`, `billing_status` (Stripe's subscription status, or null), `billing_ends_at` (when a cancelled subscription stops, or null), `card_payment` (Stripe is set up), `manage_billing` (the portal can open).
- `POST /api/v1/staff/plan/request`: records the request once and emails `ADMIN_EMAIL`. Returns the same status. A business on `unlimited` gets 409 `no_plan_needed`.
- `POST /api/v1/staff/plan/checkout`: `{"url": <Stripe Checkout>}`. 409 `card_payment_off` without Stripe, `no_plan_needed` for `unlimited`, `already_paying` with a working subscription; 502 `billing_unavailable` when Stripe fails.
- `POST /api/v1/staff/plan/portal`: `{"url": <Stripe customer portal>}`. 409 `no_billing_account` before the first card payment.
- `POST /api/v1/stripe/webhook`: for Stripe only, signed. 404 until `STRIPE_WEBHOOK_SECRET` is set.

## Not done yet

- An email to you when a business pays by card or its payments stop. For now, see Stripe's dashboard, or `python -m app.plans show <slug>`.
- Not tried against Stripe's real test mode yet: the webhook and API calls are covered by tests with Stripe's documented formats.
- Emails before the trial ends. For now the dashboard and the "set up" email carry it.
- More plans, and a cost in dollars per call to check the margin. The dashboard records tokens, not dollars, so check the OpenAI and Twilio usage pages after real calls.
