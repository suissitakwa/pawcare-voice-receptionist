import { useEffect, useState } from "react";
import { ApiRequestError } from "./api";
import { staffApi, type PlanStatus } from "./staffApi";

// The same as backend/app/plans.py: shown on the sign-up page before any plan exists.
export const PLAN_PRICE = "$49";
export const MONTHLY_CALLS = 100;
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
 * The free trial or plan on the staff dashboard. With Stripe set up, owners pay by card on
 * Stripe's Checkout page and manage it in Stripe's portal. Without it, asking to keep the
 * receptionist emails HelloEver, who send an invoice and then mark the plan paid.
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
  return (
    <section aria-labelledby="staff-plan">
      <h2 id="staff-plan">Your plan</h2>
      {returned === "paid" && (
        <p className="form-info" role="status">
          Thanks for paying. Your receptionist is on the {status.price} a month plan
          {status.plan === "trial" ? " as soon as Stripe confirms the payment (usually within a minute)" : ""}.
        </p>
      )}
      {returned === "cancelled" && <p className="muted">Payment cancelled. Nothing was charged.</p>}
      {status.state === "trial" && status.trial_ends_at && (
        <p>
          <strong>Free trial: {daysLeft(status.trial_ends_at)} days left</strong> (until {day(status.trial_ends_at)}). Then{" "}
          {status.price} a month for up to {status.calls_included} calls. Nothing is charged automatically.
        </p>
      )}
      {status.state === "trial_ended" && (
        <p className="form-alert" role="alert">
          Your free trial ended{status.trial_ends_at ? ` on ${day(status.trial_ends_at)}` : ""}. Your receptionist only takes
          messages now: it can't book or answer questions until you keep it on the {status.price} a month plan.
        </p>
      )}
      {status.state === "payment_stopped" && (
        <p className="form-alert" role="alert">
          Your card payments have stopped. Your receptionist only takes messages now: it can't book or answer questions until
          you pay for the {status.price} a month plan again.
        </p>
      )}
      {status.state === "allowance_used" && (
        <p className="form-alert" role="alert">
          You've used this month's {status.calls_included} calls. Until the 1st, your receptionist only takes messages.
        </p>
      )}
      {status.state === "active" && (
        <p>
          <strong>{status.price} a month</strong> for up to {status.calls_included} calls.
        </p>
      )}
      {status.billing_status === "past_due" && (
        <p className="form-alert" role="alert">
          Your last card payment didn't go through. Stripe will try again; update your card in Manage billing to keep your
          receptionist working.
        </p>
      )}
      <p className="muted">{calls}.</p>
      <div className="actions">
        {canPayByCard && (
          <button type="button" className="button button-primary" disabled={busy} onClick={goToStripe(staffApi.startCheckout)}>
            Pay by card ({status.price} a month)
          </button>
        )}
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
