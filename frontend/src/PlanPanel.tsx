import { useEffect, useState } from "react";
import { ApiRequestError } from "./api";
import { staffApi, type PaidPlan, type PlanStatus } from "./staffApi";

// The same as backend/app/plans.py (PAID): shown on the sign-up page before any plan exists.
export const PLANS: { plan: PaidPlan; label: string; price: string; calls: number }[] = [
  { plan: "starter", label: "Starter", price: "$49", calls: 100 },
  { plan: "growth", label: "Growth", price: "$99", calls: 300 },
  { plan: "pro", label: "Pro", price: "$149", calls: 500 },
];
export const TRIAL_DAYS = 14;

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "long", day: "numeric" });
}

function daysLeft(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
}

/** Where Stripe sent the owner back: ?billing=paid or ?billing=cancelled. Read once, then removed. */
function takeBillingReturn(): string | null {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("billing");
  if (value) {
    params.delete("billing");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
  }
  return value;
}

function leave(url: string) {
  window.location.assign(url);
}

/**
 * The free trial or plan on the staff dashboard. With Stripe set up, owners choose Starter,
 * Growth or Pro and pay by card on Stripe's Checkout page, then change plan, card or cancel in
 * Stripe's portal. Without it, asking to keep the receptionist emails HelloEver, who send an
 * invoice and then set the plan.
 */
export function PlanPanel({ token, navigate = leave }: { token: string; navigate?: (url: string) => void }) {
  const [status, setStatus] = useState<PlanStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [returned] = useState(takeBillingReturn);

  useEffect(() => {
    staffApi.plan(token).then(setStatus, () => setStatus(null));
  }, [token]);

  if (!status || status.state === "unlimited") return null;

  const failed = (err: unknown) => setError(err instanceof ApiRequestError ? err.message : "Something went wrong.");
  const ask = () => {
    setBusy(true);
    setError("");
    staffApi
      .requestPlan(token)
      .then(setStatus, failed)
      .finally(() => setBusy(false));
  };
  // Stays busy on success: the page is leaving for Stripe.
  const goToStripe = (open: (token: string) => Promise<{ url: string }>) => () => {
    setBusy(true);
    setError("");
    open(token).then(
      ({ url }) => navigate(url),
      (err) => {
        failed(err);
        setBusy(false);
      },
    );
  };

  const paying = ["active", "trialing", "past_due"].includes(status.billing_status ?? "");
  const canPayByCard = status.card_payment && !paying && (status.plan === "trial" || status.state === "payment_stopped");
  const calls = `${status.calls_this_month} of ${status.calls_included} calls this month`;
  const cheapest = status.plan_options[0]?.price ?? status.price;
  const choices = status.plan_options.filter((option) => option.card_payment);
  return (
    <section aria-labelledby="staff-plan">
      <h2 id="staff-plan">Your plan</h2>
      {returned === "paid" && (
        <p className="form-info" role="status">
          {status.plan === "trial"
            ? "Thanks for paying. Your plan starts as soon as Stripe confirms the payment (usually within a minute)."
            : `Thanks for paying. Your receptionist is on ${status.label}.`}
        </p>
      )}
      {returned === "cancelled" && <p className="muted">Payment cancelled. Nothing was charged.</p>}
      {status.state === "trial" && status.trial_ends_at && (
        <p>
          <strong>Free trial: {daysLeft(status.trial_ends_at)} days left</strong> (until {day(status.trial_ends_at)}). Then choose a
          plan, from {cheapest} a month. Nothing is charged automatically.
        </p>
      )}
      {status.state === "trial_ended" && (
        <p className="form-alert" role="alert">
          Your free trial ended{status.trial_ends_at ? ` on ${day(status.trial_ends_at)}` : ""}. Your receptionist only takes
          messages now: it can't book or answer questions until you choose a plan.
        </p>
      )}
      {status.state === "payment_stopped" && (
        <p className="form-alert" role="alert">
          Your card payments have stopped. Your receptionist only takes messages now: it can't book or answer questions until
          you choose a plan again.
        </p>
      )}
      {status.state === "allowance_used" && (
        <p className="form-alert" role="alert">
          You've used this month's {status.calls_included} calls. Until the 1st, your receptionist only takes messages.
          {status.manage_billing && " For more calls, choose a bigger plan in Manage billing."}
        </p>
      )}
      {status.state === "active" && (
        <p>
          <strong>
            {status.label}: {status.price} a month
          </strong>{" "}
          for up to {status.calls_included} calls.
          {status.manage_billing && " To change plan, choose Manage billing."}
        </p>
      )}
      {status.billing_ends_at && status.state !== "payment_stopped" && (
        <p className="form-info" role="status">
          <strong>Cancels on {day(status.billing_ends_at)}.</strong> Your receptionist works in full until then, and only
          takes messages after. To keep it, choose Manage billing, then Don't cancel subscription.
        </p>
      )}
      {status.billing_status === "past_due" && (
        <p className="form-alert" role="alert">
          Your last card payment didn't go through. Stripe will try again; update your card in Manage billing to keep your
          receptionist working.
        </p>
      )}
      <p className="muted">{calls}.</p>
      {canPayByCard && choices.length > 0 && (
        <ul className="plan-options" aria-label="Plans">
          {choices.map((option) => (
            <li key={option.plan}>
              <button
                type="button"
                className={option.plan === "growth" ? "button button-primary" : "button"}
                disabled={busy}
                onClick={goToStripe((t) => staffApi.startCheckout(t, option.plan))}
              >
                {option.label}: {option.price} a month for {option.calls} calls
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="actions">
        {!status.card_payment && status.plan === "trial" && !status.upgrade_requested_at && (
          <button type="button" className="button button-primary" disabled={busy} onClick={ask}>
            Keep my receptionist ({status.price} a month)
          </button>
        )}
        {status.manage_billing && (
          <button type="button" className="button" disabled={busy} onClick={goToStripe(staffApi.billingPortal)}>
            Manage billing
          </button>
        )}
      </div>
      {!status.card_payment && status.plan === "trial" && status.upgrade_requested_at && (
        <p className="form-info" role="status">
          Thanks: you asked to keep your receptionist on {day(status.upgrade_requested_at)}. We'll email you an invoice.
        </p>
      )}
      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
