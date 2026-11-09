import { useCallback, useEffect, useId, useState } from "react";
import { ApiRequestError } from "./api";
import { longDate, wallClock } from "./BookingForm";
import { formatPrice } from "./format";
import {
  login,
  staffApi,
  type BlockInput,
  type StaffBlock,
  type StaffBooking,
  type StaffCallback,
  type StaffMetrics,
  type StaffUser,
  type WebhookStatus,
} from "./staffApi";

// Session storage keeps the token for this tab only, and it is cleared when the tab closes.
const TOKEN_KEY = "pawcare-staff-token";

function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage can be unavailable in private browsing. Sign-in still works for this page view.
  }
}

const REASONS: Record<string, string> = {
  health: "Health question",
  behavior: "Behaviour",
  special_handling: "Special handling",
  large_dog: "Extra large dog",
  booking_help: "Booking help",
  other: "Other",
};

function errorText(error: unknown): string {
  return error instanceof ApiRequestError ? error.message : "Something went wrong.";
}

function when(local: string, end?: string): string {
  const day = longDate(local.slice(0, 10));
  return end ? `${day}, ${wallClock(local)} to ${wallClock(end)}` : `${day}, ${wallClock(local)}`;
}

function LoginForm({ onSignedIn, notice }: { onSignedIn: (token: string, staff: StaffUser) => void; notice?: string }) {
  const id = useId();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(notice ?? "");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(email, password);
      setPassword("");
      onSignedIn(result.token, result.staff);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="booking-panel staff-login" onSubmit={submit}>
      <h1>Staff sign in</h1>
      <div className="field">
        <label htmlFor={`${id}-email`}>Email</label>
        <input id={`${id}-email`} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor={`${id}-password`}>Password</label>
        <input
          id={`${id}-password`}
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </form>
  );
}

function BookingsTable({
  bookings,
  onCancel,
  busyReference,
}: {
  bookings: StaffBooking[];
  onCancel?: (reference: string) => void;
  busyReference: string | null;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  if (bookings.length === 0) return <p className="muted">No bookings.</p>;
  return (
    <div className="table-scroll">
      <table className="staff-table">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Service</th>
            <th scope="col">Dog</th>
            <th scope="col">Owner</th>
            <th scope="col">Reference</th>
            <th scope="col">Via</th>
            <th scope="col">Status</th>
            {onCancel && <th scope="col"><span className="visually-hidden">Actions</span></th>}
          </tr>
        </thead>
        <tbody>
          {bookings.map((booking) => (
            <tr key={booking.reference}>
              <td>{when(booking.local_start, booking.local_end)}</td>
              <td>
                {booking.service}
                <span className="size-note">
                  {booking.size}, {formatPrice(booking.price_cents)}
                </span>
              </td>
              <td>{booking.pet_name}</td>
              <td>
                {booking.owner_name}
                <span className="size-note">{[booking.owner_email, booking.owner_phone].filter(Boolean).join(", ")}</span>
              </td>
              <td>{booking.reference}</td>
              <td>{booking.source === "voice" ? "Voice" : "Form"}</td>
              <td>
                <span className={`status status-${booking.status}`}>{booking.status === "confirmed" ? "Confirmed" : "Cancelled"}</span>
              </td>
              {onCancel && (
                <td>
                  {booking.status === "confirmed" &&
                    (confirming === booking.reference ? (
                      <span className="inline-actions">
                        <button
                          type="button"
                          className="button button-danger"
                          disabled={busyReference === booking.reference}
                          onClick={() => {
                            setConfirming(null);
                            onCancel(booking.reference);
                          }}
                        >
                          Yes, cancel {booking.reference}
                        </button>
                        <button type="button" className="link-button" onClick={() => setConfirming(null)}>
                          Keep
                        </button>
                      </span>
                    ) : (
                      <button type="button" className="button button-secondary" onClick={() => setConfirming(booking.reference)}>
                        Cancel
                      </button>
                    ))}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BlockForm({ onAdd }: { onAdd: (input: BlockInput) => Promise<void> }) {
  const id = useId();
  const [input, setInput] = useState<BlockInput>({ start_date: "", start_time: "09:00", end_date: "", end_time: "17:00", reason: "" });
  const [busy, setBusy] = useState(false);
  function set(key: keyof BlockInput, value: string) {
    setInput((current) => ({ ...current, [key]: value, ...(key === "start_date" && !current.end_date ? { end_date: value } : {}) }));
  }
  return (
    <form
      className="block-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await onAdd(input);
          setInput((current) => ({ ...current, reason: "" }));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="field-row">
        <div className="field">
          <label htmlFor={`${id}-sd`}>From date</label>
          <input id={`${id}-sd`} type="date" value={input.start_date} onChange={(e) => set("start_date", e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-st`}>From time</label>
          <input id={`${id}-st`} type="time" value={input.start_time} onChange={(e) => set("start_time", e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-ed`}>To date</label>
          <input id={`${id}-ed`} type="date" value={input.end_date} onChange={(e) => set("end_date", e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-et`}>To time</label>
          <input id={`${id}-et`} type="time" value={input.end_time} onChange={(e) => set("end_time", e.target.value)} required />
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${id}-reason`}>Reason (optional, staff only)</label>
        <input id={`${id}-reason`} value={input.reason} maxLength={200} onChange={(e) => set("reason", e.target.value)} />
      </div>
      <div className="actions">
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? "Blocking…" : "Block this time"}
        </button>
      </div>
    </form>
  );
}

function Dashboard({ token, staff, onSignedOut }: { token: string; staff: StaffUser; onSignedOut: (notice?: string) => void }) {
  const [scope, setScope] = useState<"upcoming" | "past">("upcoming");
  const [bookings, setBookings] = useState<StaffBooking[] | null>(null);
  const [blocks, setBlocks] = useState<StaffBlock[]>([]);
  const [metrics, setMetrics] = useState<StaffMetrics | null>(null);
  const [callbacks, setCallbacks] = useState<StaffCallback[]>([]);
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);
  const [busyReference, setBusyReference] = useState<string | null>(null);

  const handle = useCallback(
    (error: unknown) => {
      if (error instanceof ApiRequestError && error.status === 401) {
        onSignedOut("Your session ended. Please sign in again.");
      } else {
        setMessage({ tone: "error", text: errorText(error) });
      }
    },
    [onSignedOut],
  );

  const load = useCallback(async () => {
    try {
      const [nextBookings, nextBlocks, nextMetrics, nextCallbacks] = await Promise.all([
        staffApi.bookings(token, scope),
        staffApi.blocks(token),
        staffApi.metrics(token),
        staffApi.callbacks(token),
      ]);
      setBookings(nextBookings);
      setBlocks(nextBlocks);
      setMetrics(nextMetrics);
      setCallbacks(nextCallbacks);
    } catch (error) {
      handle(error);
    }
  }, [token, scope, handle]);

  useEffect(() => {
    load();
  }, [load]);

  async function cancel(reference: string) {
    setBusyReference(reference);
    try {
      await staffApi.cancel(token, reference);
      setMessage({ tone: "info", text: `Booking ${reference} was cancelled, and its time is open again.` });
      await load();
    } catch (error) {
      handle(error);
    } finally {
      setBusyReference(null);
    }
  }

  async function addBlock(input: BlockInput) {
    try {
      const result = await staffApi.addBlock(token, input);
      const clash = result.conflicts.length;
      setMessage(
        clash
          ? {
              tone: "error",
              text: `Time blocked, but ${clash} existing booking${clash === 1 ? "" : "s"} overlap${clash === 1 ? "s" : ""} it: ${result.conflicts
                .map((c) => c.reference)
                .join(", ")}. Contact the customer or cancel the booking.`,
            }
          : { tone: "info", text: "Time blocked. Customers can no longer book it." },
      );
      await load();
    } catch (error) {
      handle(error);
    }
  }

  async function completeCallback(id: string) {
    try {
      await staffApi.completeCallback(token, id);
      setMessage({ tone: "info", text: "Callback marked as done." });
      await load();
    } catch (error) {
      handle(error);
    }
  }

  async function removeBlock(id: string) {
    try {
      await staffApi.removeBlock(token, id);
      setMessage({ tone: "info", text: "Block removed. That time can be booked again." });
      await load();
    } catch (error) {
      handle(error);
    }
  }

  return (
    <div className="staff-dashboard">
      <div className="staff-header">
        <h1>Dashboard</h1>
        <p>
          Signed in as {staff.display_name}{" "}
          <button
            type="button"
            className="link-button"
            onClick={async () => {
              await staffApi.logout(token).catch(() => undefined);
              onSignedOut();
            }}
          >
            Sign out
          </button>
        </p>
      </div>
      {message && (
        <p className={message.tone === "error" ? "form-alert" : "form-info"} role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      )}
      {metrics && (
        <dl className="metric-grid">
          <div>
            <dt>Upcoming bookings</dt>
            <dd>{metrics.upcoming_bookings}</dd>
          </div>
          <div>
            <dt>Booked in the last 7 days</dt>
            <dd>{metrics.bookings_last_7_days}</dd>
          </div>
          <div>
            <dt>Cancelled in the last 7 days</dt>
            <dd>{metrics.cancellations_last_7_days}</dd>
          </div>
          <div>
            <dt>Open callbacks</dt>
            <dd>{metrics.open_callbacks}</dd>
          </div>
          <div>
            <dt>Voice calls in the last 7 days</dt>
            <dd>{metrics.voice_sessions_last_7_days}</dd>
          </div>
          <div>
            <dt>Booked by voice in the last 7 days</dt>
            <dd>{metrics.voice_bookings_last_7_days}</dd>
          </div>
          <div>
            <dt>Assistant tool failures in the last 7 days</dt>
            <dd>{metrics.tool_failures_last_7_days}</dd>
          </div>
          <div>
            <dt>Voice reply time, median (90th percentile)</dt>
            <dd>
              {metrics.voice_reply_median_ms === null || metrics.voice_reply_p90_ms === null
                ? "Not measured yet"
                : `${seconds(metrics.voice_reply_median_ms)} (${seconds(metrics.voice_reply_p90_ms)}), ${metrics.voice_replies_measured} replies`}
            </dd>
          </div>
        </dl>
      )}
      <section aria-labelledby="staff-bookings">
        <div className="section-head">
          <h2 id="staff-bookings">Bookings</h2>
          <div className="choice-grid" role="radiogroup" aria-label="Which bookings">
            {(["upcoming", "past"] as const).map((value) => (
              <label key={value} className="choice">
                <input type="radio" name="scope" checked={scope === value} onChange={() => setScope(value)} />
                <span>{value === "upcoming" ? "Upcoming" : "Past"}</span>
              </label>
            ))}
          </div>
        </div>
        {bookings === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <BookingsTable bookings={bookings} onCancel={scope === "upcoming" ? cancel : undefined} busyReference={busyReference} />
        )}
      </section>
      <section aria-labelledby="staff-callbacks">
        <h2 id="staff-callbacks">Callback requests</h2>
        {callbacks.length === 0 ? (
          <p className="muted">No open callback requests.</p>
        ) : (
          <ul className="block-list">
            {callbacks.map((callback) => (
              <li key={callback.id}>
                <span>
                  <strong>{callback.name}</strong>, {[callback.phone, callback.email].filter(Boolean).join(", ")}
                  <span className="size-note">
                    {REASONS[callback.reason] ?? callback.reason}
                    {callback.note ? `: ${callback.note}` : ""}. Requested {new Date(callback.created_at).toLocaleString()}
                  </span>
                </span>
                <button type="button" className="button button-secondary" onClick={() => completeCallback(callback.id)}>
                  Mark done
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="staff-blocks">
        <h2 id="staff-blocks">Blocked time</h2>
        <p className="muted">Times are in the business time zone. Blocked time cannot be booked online.</p>
        {blocks.length === 0 ? (
          <p className="muted">Nothing blocked.</p>
        ) : (
          <ul className="block-list">
            {blocks.map((block) => (
              <li key={block.id}>
                <span>
                  {when(block.local_start)} to {when(block.local_end)}
                  {block.reason && <span className="size-note">{block.reason}</span>}
                </span>
                <button type="button" className="button button-secondary" onClick={() => removeBlock(block.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <BlockForm onAdd={addBlock} />
      </section>
      <AutomationPanel token={token} />
    </div>
  );
}

// Delivery of booking and callback events to an automation tool such as n8n.
function AutomationPanel({ token }: { token: string }) {
  const [status, setStatus] = useState<WebhookStatus | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    staffApi.webhooks(token).then(setStatus, () => setMessage("Automation status is unavailable."));
  }, [token]);

  useEffect(refresh, [refresh]);

  async function sendTest() {
    setBusy(true);
    setMessage("");
    try {
      await staffApi.sendTestEvent(token);
      setMessage("Test event queued. It is sent within a few seconds.");
      refresh();
    } catch (err) {
      setMessage(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="staff-automations">
      <h2 id="staff-automations">Automations</h2>
      <p className="muted">New bookings, cancellations, and callback requests are sent to your automation webhook, for example n8n.</p>
      {status && (
        <dl className="metric-grid">
          <div>
            <dt>Webhook</dt>
            <dd>{status.configured ? "Connected" : `Off: ${status.problem}`}</dd>
          </div>
          <div>
            <dt>Waiting to send</dt>
            <dd>{status.pending}</dd>
          </div>
          <div>
            <dt>Sent in the last 7 days</dt>
            <dd>{status.delivered_last_7_days}</dd>
          </div>
          <div>
            <dt>Failed in the last 7 days</dt>
            <dd>
              {status.failed_last_7_days}
              {status.last_error && <span className="size-note">Last error: {status.last_error}</span>}
            </dd>
          </div>
        </dl>
      )}
      <div className="actions">
        <button type="button" className="button button-secondary" disabled={busy} onClick={() => void sendTest()}>
          {busy ? "Queuing…" : "Send test event"}
        </button>
      </div>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
    </section>
  );
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

export default function StaffApp() {
  const [token, setToken] = useState<string | null>(readToken);
  const [staff, setStaff] = useState<StaffUser | null>(null);
  const [notice, setNotice] = useState<string | undefined>();

  const signOut = useCallback((text?: string) => {
    writeToken(null);
    setToken(null);
    setStaff(null);
    setNotice(text);
  }, []);

  useEffect(() => {
    document.title = "Staff dashboard";
    if (token && !staff) {
      staffApi.me(token).then(setStaff).catch(() => signOut("Please sign in."));
    }
  }, [token, staff, signOut]);

  return (
    <main id="main" className="container staff-page">
      <p className="demo-banner staff-banner" role="note">
        Demonstration dashboard. Bookings shown here were made on the public demo site.
      </p>
      {token && staff ? (
        <Dashboard token={token} staff={staff} onSignedOut={signOut} />
      ) : token ? (
        <p className="muted">Loading…</p>
      ) : (
        <LoginForm
          notice={notice}
          onSignedIn={(next, user) => {
            writeToken(next);
            setToken(next);
            setStaff(user);
            setNotice(undefined);
          }}
        />
      )}
      <p>
        <a href="/">Back to the website</a>
      </p>
    </main>
  );
}
