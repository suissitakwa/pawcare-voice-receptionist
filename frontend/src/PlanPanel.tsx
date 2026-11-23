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

/**
 * The free trial or plan on the staff dashboard. Nothing is charged here: asking to keep the
 * receptionist emails HelloEver, who send an invoice and then mark the plan paid.
 */
export function PlanPanel({ token }: { token: string }) {
  const [status, setStatus] = useState<PlanStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    staffApi.plan(token).then(setStatus, () => setStatus(null));
  }, [token]);

  if (!status || status.state === "unlimited") return null;

  const ask = () => {
    setBusy(true);
    setError("");
    staffApi
      .requestPlan(token)
      .then(setStatus, (err) => setError(err instanceof ApiRequestError ? err.message : "Something went wrong."))
      .finally(() => setBusy(false));
  };

  const calls = `${status.calls_this_month} of ${status.calls_included} calls this month`;
  return (
    <section aria-labelledby="staff-plan">
      <h2 id="staff-plan">Your plan</h2>
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
      <p className="muted">{calls}.</p>
      {status.plan === "trial" &&
        (status.upgrade_requested_at ? (
          <p className="form-info" role="status">
            Thanks: you asked to keep your receptionist on {day(status.upgrade_requested_at)}. We'll email you an invoice.
          </p>
        ) : (
          <div className="actions">
            <button type="button" className="button button-primary" disabled={busy} onClick={ask}>
              Keep my receptionist ({status.price} a month)
            </button>
          </div>
        ))}
      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
