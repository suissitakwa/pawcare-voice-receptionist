import { useId, useState } from "react";
import { ApiRequestError } from "./api";

const MIN_LENGTH = 12;

function message(error: unknown): string {
  return error instanceof ApiRequestError ? error.message : "Something went wrong. Please try again.";
}

/** The one-time token from a reset link (?reset=…), removed from the address bar once read. */
export function takeResetToken(): string | null {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("reset");
  if (!token) return null;
  params.delete("reset");
  const query = params.toString();
  window.history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : "") + window.location.hash);
  return token;
}

/** "Forgot password?": asks for the email and always answers the same way. */
export function ForgotPasswordForm({
  send,
  onBack,
}: {
  send: (email: string) => Promise<unknown>;
  onBack: () => void;
}) {
  const id = useId();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await send(email.trim());
      setSent(true);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="booking-panel staff-login" onSubmit={submit}>
      <h1>Reset your password</h1>
      {sent ? (
        <p className="form-info" role="status">
          If there's an account for {email.trim()}, we've emailed it a link to choose a new password. It works once,
          for one hour. Check your spam folder if it doesn't arrive in a few minutes.
        </p>
      ) : (
        <>
          <p className="muted">Enter the email you sign in with, and we'll send you a link.</p>
          <div className="field">
            <label htmlFor={`${id}-email`}>Email</label>
            <input
              id={`${id}-email`}
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
        </>
      )}
      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        {!sent && (
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
        )}
        <button type="button" className="link-button" onClick={onBack}>
          Back to sign in
        </button>
      </div>
    </form>
  );
}

/** Opened from the emailed link: choose and confirm a new password. */
export function NewPasswordForm({
  token,
  reset,
  onDone,
}: {
  token: string;
  reset: (token: string, password: string) => Promise<unknown>;
  onDone: () => void;
}) {
  const id = useId();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password.length < MIN_LENGTH) {
      setError(`Use at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (password !== again) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await reset(token, password);
      setPassword("");
      setAgain("");
      setDone(true);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="booking-panel staff-login">
        <h1>Password changed</h1>
        <p className="form-info" role="status">
          Your new password is set, and you were signed out everywhere else. Sign in with it now.
        </p>
        <div className="actions">
          <button type="button" className="button button-primary" onClick={onDone}>
            Sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="booking-panel staff-login" onSubmit={submit} noValidate>
      <h1>Choose a new password</h1>
      <div className="field">
        <label htmlFor={`${id}-new`}>New password</label>
        <input
          id={`${id}-new`}
          type="password"
          autoComplete="new-password"
          aria-describedby={`${id}-hint`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <p className="field-hint" id={`${id}-hint`}>
          At least {MIN_LENGTH} characters.
        </p>
      </div>
      <div className="field">
        <label htmlFor={`${id}-again`}>New password again</label>
        <input
          id={`${id}-again`}
          type="password"
          autoComplete="new-password"
          value={again}
          onChange={(e) => setAgain(e.target.value)}
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
          {busy ? "Saving…" : "Set new password"}
        </button>
        <button type="button" className="link-button" onClick={onDone}>
          Back to sign in
        </button>
      </div>
    </form>
  );
}
