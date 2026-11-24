import { useCallback, useEffect, useId, useState } from "react";
import { ApiRequestError, INDUSTRY_LABELS } from "./api";
import {
  adminApi,
  adminLogin,
  type AdminAccount,
  type AdminAccountDetail,
  type AdminAction,
  type AdminUser,
  type Plan,
} from "./adminApi";

const TOKEN_KEY = "helloever-admin-token";

// Session storage: the token lives in this tab only and is gone when it closes.
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
    // Storage can be unavailable in private browsing; sign-in still works for this page view.
  }
}

function errorText(error: unknown): string {
  return error instanceof ApiRequestError ? error.message : "Something went wrong.";
}

function day(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "Never";
}

function moment(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const PROFILE_STATUS: Record<string, string> = { draft: "Filling in", submitted: "Submitted" };
const PLANS: Record<Plan, string> = { trial: "Free trial", starter: "Starter", unlimited: "Unlimited" };
const ACTIONS: Record<string, string> = {
  "account.updated": "Edited account",
  "account.unlocked": "Unlocked account",
  "account.signed_out": "Signed account out",
  "account.deleted": "Deleted account",
  "business.plan_changed": "Changed plan",
};

function stage(account: AdminAccount): string {
  if (account.business_slug) return "Live";
  return PROFILE_STATUS[account.profile_status ?? ""] ?? "No profile";
}

function LoginForm({ onSignedIn, notice }: { onSignedIn: (token: string, admin: AdminUser) => void; notice?: string }) {
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
      const result = await adminLogin(email, password);
      setPassword("");
      onSignedIn(result.token, result.admin);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="booking-panel staff-login" onSubmit={submit}>
      <h1>Admin sign in</h1>
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

function AccountPanel({
  token,
  account,
  onChanged,
  onDeleted,
  onClose,
}: {
  token: string;
  account: AdminAccountDetail;
  onChanged: (next: AdminAccountDetail, note: string) => void;
  onDeleted: (note: string) => void;
  onClose: () => void;
}) {
  const id = useId();
  const [fullName, setFullName] = useState(account.full_name);
  const [email, setEmail] = useState(account.email);
  const [plan, setPlan] = useState<Plan>(account.plan ?? "trial");
  const [trialDays, setTrialDays] = useState("14");
  const [confirm, setConfirm] = useState("");
  const [withBusiness, setWithBusiness] = useState(!account.business_is_protected);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setFullName(account.full_name);
    setEmail(account.email);
    setPlan(account.plan ?? "trial");
  }, [account]);

  async function run<T>(action: () => Promise<T>, done: (result: T) => void) {
    setBusy(true);
    setError("");
    try {
      done(await action());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function save(event: React.FormEvent) {
    event.preventDefault();
    const changes = {
      ...(fullName.trim() !== account.full_name ? { full_name: fullName.trim() } : {}),
      ...(email.trim().toLowerCase() !== account.email ? { email: email.trim() } : {}),
    };
    if (Object.keys(changes).length === 0) return;
    run(() => adminApi.update(token, account.id, changes), (next) => onChanged(next, "Account saved."));
  }

  function savePlan(event: React.FormEvent) {
    event.preventDefault();
    const slug = account.business_slug;
    if (!slug) return;
    run(
      () => adminApi.setPlan(token, slug, plan, Number(trialDays) || undefined),
      (next) => next && onChanged(next, `Plan set to ${PLANS[plan]}.`),
    );
  }

  function remove(event: React.FormEvent) {
    event.preventDefault();
    run(
      () => adminApi.remove(token, account.id, confirm, withBusiness),
      (result) =>
        onDeleted(
          result.deleted_business_slug
            ? `Deleted ${result.deleted_email} and its business ${result.deleted_business_slug} (${result.deleted_bookings} bookings, ${result.released_phone_numbers} phone numbers).`
            : `Deleted ${result.deleted_email}.`,
        ),
    );
  }

  const confirmed = confirm.trim().toLowerCase() === account.email;

  return (
    <section className="block-form admin-account" aria-labelledby={`${id}-title`}>
      <div className="section-head">
        <h2 id={`${id}-title`}>{account.full_name}</h2>
        <button type="button" className="link-button" onClick={onClose}>
          Close
        </button>
      </div>
      <dl className="admin-facts">
        <div><dt>Email</dt><dd>{account.email}</dd></div>
        <div><dt>Signed up</dt><dd>{day(account.created_at)}</dd></div>
        <div><dt>Last sign-in</dt><dd>{day(account.last_sign_in_at)}</dd></div>
        <div><dt>Stage</dt><dd>{stage(account)}{account.locked ? " · locked" : ""}</dd></div>
        <div><dt>Business</dt><dd>{account.business_name ?? "Not named yet"}{account.business_type ? ` (${INDUSTRY_LABELS[account.business_type] ?? account.business_type})` : ""}</dd></div>
        {account.business_slug && (
          <>
            <div><dt>Address</dt><dd><a href={`/b/${account.business_slug}/`}>/b/{account.business_slug}</a></dd></div>
            <div><dt>Plan</dt><dd>{account.plan ? PLANS[account.plan] : "—"}{account.trial_ends_at ? `, ends ${day(account.trial_ends_at)}` : ""}</dd></div>
            <div><dt>Bookings · calls</dt><dd>{account.bookings} · {account.calls}</dd></div>
            <div><dt>Phone numbers</dt><dd>{account.phone_numbers.join(", ") || "None"}</dd></div>
            <div><dt>Staff logins</dt><dd>{account.staff_logins.map((s) => s.email).join(", ") || "None"}</dd></div>
          </>
        )}
      </dl>

      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}

      <form onSubmit={save}>
        <h3>Edit account</h3>
        <div className="field">
          <label htmlFor={`${id}-name`}>Full name</label>
          <input id={`${id}-name`} value={fullName} maxLength={80} onChange={(e) => setFullName(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-email`}>Sign-in email</label>
          <input id={`${id}-email`} type="email" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        {account.staff_logins.length > 0 && <p className="muted">Staff dashboard logins keep their own email.</p>}
        <div className="actions inline-actions">
          <button type="submit" className="button button-primary" disabled={busy}>
            Save
          </button>
          {account.locked && (
            <button type="button" className="button" disabled={busy} onClick={() => run(() => adminApi.unlock(token, account.id), (next) => onChanged(next, "Account unlocked."))}>
              Unlock
            </button>
          )}
          <button type="button" className="button" disabled={busy} onClick={() => run(() => adminApi.signOut(token, account.id), (next) => onChanged(next, "Signed out everywhere."))}>
            Sign out everywhere
          </button>
        </div>
      </form>

      {account.business_slug && (
        <form onSubmit={savePlan}>
          <h3>Plan</h3>
          <div className="field">
            <label htmlFor={`${id}-plan`}>Plan</label>
            <select id={`${id}-plan`} value={plan} onChange={(e) => setPlan(e.target.value as Plan)}>
              {Object.entries(PLANS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          {plan === "trial" && (
            <div className="field">
              <label htmlFor={`${id}-days`}>Trial days from today</label>
              <input id={`${id}-days`} type="number" min={1} max={365} value={trialDays} onChange={(e) => setTrialDays(e.target.value)} required />
            </div>
          )}
          <div className="actions">
            <button type="submit" className="button" disabled={busy}>
              Save plan
            </button>
          </div>
        </form>
      )}

      <form onSubmit={remove}>
        <h3>Delete account</h3>
        <p className="muted">This can't be undone. The sign-in, profile and uploaded documents are deleted.</p>
        {account.business_slug && (
          <label className="admin-check">
            <input
              type="checkbox"
              checked={withBusiness}
              disabled={account.business_is_protected}
              onChange={(e) => setWithBusiness(e.target.checked)}
            />{" "}
            {account.business_is_protected
              ? "Its business is the demo and stays."
              : `Also delete the live business ${account.business_slug}, with its ${account.bookings} bookings, calls, staff logins and phone numbers`}
          </label>
        )}
        <div className="field">
          <label htmlFor={`${id}-confirm`}>Type {account.email} to confirm</label>
          <input id={`${id}-confirm`} value={confirm} autoComplete="off" onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <div className="actions">
          <button type="submit" className="button button-danger" disabled={busy || !confirmed}>
            Delete account
          </button>
        </div>
      </form>
    </section>
  );
}

function Console({ token, admin, onSignedOut }: { token: string; admin: AdminUser; onSignedOut: (notice?: string) => void }) {
  const searchId = useId();
  const [query, setQuery] = useState("");
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [actions, setActions] = useState<AdminAction[]>([]);
  const [selected, setSelected] = useState<AdminAccountDetail | null>(null);
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);

  const failed = useCallback(
    (err: unknown) => {
      if (err instanceof ApiRequestError && err.status === 401) onSignedOut("Your session ended. Please sign in again.");
      else setMessage({ tone: "error", text: errorText(err) });
    },
    [onSignedOut],
  );

  const load = useCallback(
    (q: string) => {
      adminApi.accounts(token, q).then(setAccounts).catch(failed);
      adminApi.actions(token).then(setActions).catch(failed);
    },
    [token, failed],
  );

  useEffect(() => load(""), [load]);

  function open(id: string) {
    setMessage(null);
    adminApi.account(token, id).then(setSelected).catch(failed);
  }

  async function signOut() {
    await adminApi.logout(token).catch(() => undefined);
    onSignedOut();
  }

  return (
    <div className="staff-dashboard">
      <div className="staff-header">
        <h1>Accounts</h1>
        <p>
          Signed in as {admin.display_name}.{" "}
          <button type="button" className="link-button" onClick={signOut}>
            Sign out
          </button>
        </p>
      </div>
      {message && (
        <p className={message.tone === "error" ? "form-alert" : "notice"} role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      )}

      {selected && (
        <AccountPanel
          key={selected.id}
          token={token}
          account={selected}
          onClose={() => setSelected(null)}
          onChanged={(next, note) => {
            setSelected(next);
            setMessage({ tone: "info", text: note });
            load(query);
          }}
          onDeleted={(note) => {
            setSelected(null);
            setMessage({ tone: "info", text: note });
            load(query);
          }}
        />
      )}

      <section>
        <form
          className="section-head"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            load(query);
          }}
        >
          <h2>Sign-ups{accounts ? ` (${accounts.length})` : ""}</h2>
          <div className="inline-actions">
            <label htmlFor={searchId} className="visually-hidden">
              Search accounts
            </label>
            <input id={searchId} type="search" placeholder="Email, name or business" value={query} maxLength={120} onChange={(e) => setQuery(e.target.value)} />
            <button type="submit" className="button">
              Search
            </button>
          </div>
        </form>
        {accounts === null ? (
          <p className="muted">Loading…</p>
        ) : accounts.length === 0 ? (
          <p className="muted">No accounts match.</p>
        ) : (
          <div className="table-scroll">
            <table className="staff-table">
              <thead>
                <tr>
                  <th>Owner</th>
                  <th>Business</th>
                  <th>Stage</th>
                  <th>Plan</th>
                  <th>Signed up</th>
                  <th>Last sign-in</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.id}>
                    <td>
                      {account.full_name}
                      <br />
                      <span className="muted">{account.email}</span>
                    </td>
                    <td>{account.business_name ?? "—"}</td>
                    <td>
                      {stage(account)}
                      {account.locked && <span className="status status-cancelled"> Locked</span>}
                    </td>
                    <td>{account.plan ? PLANS[account.plan] : "—"}</td>
                    <td>{day(account.created_at)}</td>
                    <td>{day(account.last_sign_in_at)}</td>
                    <td>
                      <button type="button" className="button" onClick={() => open(account.id)} aria-label={`Manage ${account.email}`}>
                        Manage
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2>Recent admin changes</h2>
        {actions.length === 0 ? (
          <p className="muted">None yet.</p>
        ) : (
          <ul className="block-list">
            {actions.map((action, index) => (
              <li key={`${action.created_at}-${index}`}>
                <span>
                  {ACTIONS[action.action] ?? action.action}: {action.target}
                </span>
                <span className="muted">
                  {action.admin_email}, {moment(action.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default function AdminApp() {
  const [token, setToken] = useState<string | null>(readToken);
  const [admin, setAdmin] = useState<AdminUser | null>(null);
  const [notice, setNotice] = useState<string | undefined>();

  const signOut = useCallback((text?: string) => {
    writeToken(null);
    setToken(null);
    setAdmin(null);
    setNotice(text);
  }, []);

  useEffect(() => {
    document.title = "Admin";
    if (token && !admin) {
      adminApi
        .me(token)
        .then(setAdmin)
        .catch(() => signOut("Please sign in."));
    }
  }, [token, admin, signOut]);

  return (
    <main id="main" className="container staff-page">
      {token && admin ? (
        <Console token={token} admin={admin} onSignedOut={signOut} />
      ) : token ? (
        <p className="muted">Loading…</p>
      ) : (
        <LoginForm
          notice={notice}
          onSignedIn={(next, user) => {
            writeToken(next);
            setToken(next);
            setAdmin(user);
            setNotice(undefined);
          }}
        />
      )}
    </main>
  );
}
