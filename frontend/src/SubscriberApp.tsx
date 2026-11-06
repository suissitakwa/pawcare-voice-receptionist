import { useCallback, useEffect, useId, useState } from "react";
import { ApiRequestError } from "./api";
import { subscriberApi, type Account, type FaqItem, type Profile, type ProfileDetails, type ProfileOptions } from "./subscriberApi";

// Session storage keeps the token for this tab only, and it is cleared when the tab closes.
const TOKEN_KEY = "receptionist-subscriber-token";

// Kept short on purpose. More options are added as businesses ask for them.
export const STEPS = [
  { id: "business", title: "Your business" },
  { id: "calendar", title: "Your calendar" },
  { id: "knowledge", title: "What the receptionist should know" },
  { id: "voice", title: "Voice" },
  { id: "review", title: "Review and submit" },
] as const;

// Which step fixes each item the backend reports as missing.
const MISSING: Record<string, { step: number; label: string }> = {
  business_name: { step: 0, label: "Business name" },
  business_type: { step: 0, label: "Business type" },
  business_phone: { step: 0, label: "Business phone number" },
  calendar_provider: { step: 1, label: "Calendar" },
  knowledge: { step: 2, label: "A website, document, or question and answer" },
  voice: { step: 3, label: "Choose a voice" },
  ai_disclosure_ack: { step: 4, label: "AI disclosure agreement" },
};

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

function errorText(error: unknown): string {
  return error instanceof ApiRequestError ? error.message : "Something went wrong.";
}

function orNull(value: string | null): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed === "" ? null : trimmed;
}

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function AuthForm({ onSignedIn }: { onSignedIn: (token: string, account: Account) => void }) {
  const id = useId();
  const [mode, setMode] = useState<"signup" | "signin">("signup");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [terms, setTerms] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result =
        mode === "signup"
          ? await subscriberApi.signUp(name, email, password, terms)
          : await subscriberApi.signIn(email, password);
      setPassword("");
      onSignedIn(result.token, result.account);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="booking-panel staff-login" onSubmit={submit}>
      <h1>{mode === "signup" ? "Set up your AI receptionist" : "Sign in"}</h1>
      <p className="muted">
        {mode === "signup"
          ? "For pet groomers, hair salons, and barbershops. Create an account, then tell us about your business."
          : "Continue setting up your receptionist."}
      </p>
      {mode === "signup" && (
        <div className="field">
          <label htmlFor={`${id}-name`}>Your name</label>
          <input id={`${id}-name`} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
        </div>
      )}
      <div className="field">
        <label htmlFor={`${id}-email`}>Email</label>
        <input id={`${id}-email`} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor={`${id}-password`}>Password</label>
        <input
          id={`${id}-password`}
          type="password"
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          minLength={mode === "signup" ? 12 : undefined}
          aria-describedby={mode === "signup" ? `${id}-password-hint` : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {mode === "signup" && (
          <p className="field-hint" id={`${id}-password-hint`}>
            At least 12 characters.
          </p>
        )}
      </div>
      {mode === "signup" && (
        <div className="field check-field">
          <input id={`${id}-terms`} type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} required />
          <label htmlFor={`${id}-terms`}>I accept the terms of service and privacy policy.</label>
        </div>
      )}
      {error && (
        <p className="form-alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? "Please wait…" : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setMode(mode === "signup" ? "signin" : "signup");
            setError("");
          }}
        >
          {mode === "signup" ? "I already have an account" : "Create an account instead"}
        </button>
      </div>
    </form>
  );
}

interface Draft {
  business_name: string;
  business_type: string;
  details: ProfileDetails;
}

function toDraft(profile: Profile): Draft {
  return { business_name: profile.business_name ?? "", business_type: profile.business_type ?? "", details: profile.details };
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: (props: { id: string; "aria-describedby"?: string }) => React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({ id, "aria-describedby": hint ? `${id}-hint` : undefined })}
      {hint && (
        <p className="field-hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
    </div>
  );
}

function Choices({
  legend,
  options,
  selected,
  onChange,
}: {
  legend: string;
  options: { value: string; label: string }[];
  selected: string | null;
  onChange: (value: string) => void;
}) {
  const name = useId();
  return (
    <fieldset className="choice-group">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="check-option">
          <input type="radio" name={name} value={option.value} checked={selected === option.value} onChange={() => onChange(option.value)} />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

function FaqEditor({ faqs, onChange }: { faqs: FaqItem[]; onChange: (faqs: FaqItem[]) => void }) {
  const update = (index: number, change: Partial<FaqItem>) => onChange(faqs.map((f, i) => (i === index ? { ...f, ...change } : f)));
  return (
    <div>
      {faqs.map((faq, index) => (
        <fieldset key={index} className="block-form">
          <legend>Question {index + 1}</legend>
          <Field label="Question">
            {(props) => <input {...props} value={faq.question} maxLength={200} onChange={(e) => update(index, { question: e.target.value })} />}
          </Field>
          <Field label="Answer">
            {(props) => (
              <textarea {...props} rows={2} maxLength={1000} value={faq.answer} onChange={(e) => update(index, { answer: e.target.value })} />
            )}
          </Field>
          <button type="button" className="link-button" onClick={() => onChange(faqs.filter((_, i) => i !== index))}>
            Remove question {index + 1}
          </button>
        </fieldset>
      ))}
      <button type="button" className="button button-secondary" onClick={() => onChange([...faqs, { question: "", answer: "" }])}>
        Add a question
      </button>
    </div>
  );
}

// Blank text and half-filled questions become absent, so a step can be saved part-done.
function cleanDetails(details: ProfileDetails, urls: string): ProfileDetails {
  return {
    ...details,
    business_phone: orNull(details.business_phone),
    website_url: orNull(details.website_url),
    calendar_other: details.calendar_provider === "other" ? orNull(details.calendar_other) : null,
    knowledge_urls: lines(urls),
    faqs: details.faqs.filter((f) => f.question.trim() !== "" && f.answer.trim() !== ""),
  };
}

function ProfileForm({ token, options, onSignOut }: { token: string; options: ProfileOptions; onSignOut: (notice?: string) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [step, setStep] = useState(0);
  const [urls, setUrls] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiRequestError && err.status === 401) onSignOut("Your session has ended. Please sign in again.");
      else setError(errorText(err));
    },
    [onSignOut],
  );

  const load = useCallback((next: Profile) => {
    setProfile(next);
    setDraft(toDraft(next));
    setUrls(next.details.knowledge_urls.join("\n"));
  }, []);

  useEffect(() => {
    subscriberApi.profile(token).then(load, handleError);
  }, [token, load, handleError]);

  if (!profile || !draft) {
    return error ? (
      <p className="form-alert" role="alert">
        {error}
      </p>
    ) : (
      <p className="muted" role="status">
        Loading your profile…
      </p>
    );
  }

  const details = draft.details;
  const setDetails = (change: Partial<ProfileDetails>) => setDraft({ ...draft, details: { ...details, ...change } });

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      await action();
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  const save = (next?: number) =>
    run(async () => {
      if (!draft) return;
      load(
        await subscriberApi.save(token, {
          business_name: orNull(draft.business_name),
          business_type: draft.business_type || null,
          details: cleanDetails(draft.details, urls),
        }),
      );
      setSaved("Saved.");
      if (next !== undefined) setStep(next);
    });

  const upload = (files: FileList | null) =>
    run(async () => {
      for (const file of Array.from(files ?? [])) {
        if (file.size > options.max_document_bytes) throw new ApiRequestError("document_too_large", `${file.name} is larger than 5 MB.`);
        await subscriberApi.upload(token, file);
      }
      setProfile(await subscriberApi.profile(token));
    });

  const removeDocument = (id: string) =>
    run(async () => {
      await subscriberApi.removeDocument(token, id);
      setProfile(await subscriberApi.profile(token));
    });

  // The agreement is part of the review step, so it is saved just before submitting.
  const submit = () =>
    run(async () => {
      if (!draft) return;
      await subscriberApi.save(token, { details: { ai_disclosure_ack: draft.details.ai_disclosure_ack } });
      load(await subscriberApi.submit(token));
    });

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const stillMissing = profile.missing.filter((item) => item !== "ai_disclosure_ack");
  const canSubmit = stillMissing.length === 0 && details.ai_disclosure_ack;

  return (
    <div className="profile-layout">
      <nav aria-label="Profile steps">
        <ol className="step-list">
          {STEPS.map((s, index) => (
            <li key={s.id}>
              <button type="button" className="link-button" aria-current={index === step ? "step" : undefined} onClick={() => setStep(index)}>
                {s.title}
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <form
        className="booking-panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (!isLast) void save(step + 1);
        }}
      >
        <h2>{current.title}</h2>

        {current.id === "business" && (
          <>
            <Field label="Business name">
              {(props) => (
                <input {...props} maxLength={120} value={draft.business_name} onChange={(e) => setDraft({ ...draft, business_name: e.target.value })} />
              )}
            </Field>
            <Choices
              legend="Type of business"
              options={options.business_types}
              selected={draft.business_type || null}
              onChange={(value) => setDraft({ ...draft, business_type: value })}
            />
            <Field label="Business phone number" hint="The number your customers call today.">
              {(props) => <input {...props} type="tel" value={details.business_phone ?? ""} onChange={(e) => setDetails({ business_phone: e.target.value })} />}
            </Field>
            <Field label="Website (optional)">
              {(props) => (
                <input {...props} type="url" placeholder="https://" value={details.website_url ?? ""} onChange={(e) => setDetails({ website_url: e.target.value })} />
              )}
            </Field>
          </>
        )}

        {current.id === "calendar" && (
          <>
            <Field label="Calendar you book appointments in" hint="We connect to it during setup, so bookings never clash.">
              {(props) => (
                <select {...props} value={details.calendar_provider ?? ""} onChange={(e) => setDetails({ calendar_provider: e.target.value || null })}>
                  <option value="">Choose your calendar</option>
                  {options.calendar_providers.map((provider) => (
                    <option key={provider.value} value={provider.value}>
                      {provider.label}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {details.calendar_provider === "other" && (
              <Field label="Which system?">
                {(props) => <input {...props} maxLength={60} value={details.calendar_other ?? ""} onChange={(e) => setDetails({ calendar_other: e.target.value })} />}
              </Field>
            )}
          </>
        )}

        {current.id === "knowledge" && (
          <>
            <p className="muted">Give the receptionist at least one source: web pages, documents, or your own questions and answers.</p>
            <Field label="Web pages to learn from" hint="One address per line, up to 10. For example your services or FAQ page.">
              {(props) => <textarea {...props} rows={3} value={urls} onChange={(e) => setUrls(e.target.value)} />}
            </Field>
            <div className="field">
              <span className="field-label">Documents</span>
              <p className="field-hint">PDF, Word (.docx), or text files up to 5 MB each, such as a price list or policies.</p>
              {profile.documents.length > 0 && (
                <ul className="block-list">
                  {profile.documents.map((doc) => (
                    <li key={doc.id} className="inline-actions">
                      <span>
                        {doc.filename} ({fileSize(doc.size_bytes)})
                      </span>
                      <button type="button" className="link-button" onClick={() => void removeDocument(doc.id)} disabled={busy}>
                        Remove {doc.filename}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <label className="button button-secondary file-button">
                Upload documents
                <input
                  type="file"
                  className="visually-hidden"
                  multiple
                  accept=".pdf,.docx,.txt,.md,application/pdf,text/plain,text/markdown"
                  onChange={(e) => {
                    void upload(e.target.files);
                    e.target.value = "";
                  }}
                />
              </label>
            </div>
            <h3>Questions customers ask</h3>
            <FaqEditor faqs={details.faqs} onChange={(faqs) => setDetails({ faqs })} />
          </>
        )}

        {current.id === "voice" && (
          <Choices
            legend="How should your receptionist sound?"
            options={options.voices}
            selected={details.voice}
            onChange={(voice) => setDetails({ voice })}
          />
        )}

        {current.id === "review" && (
          <>
            {profile.status === "submitted" ? (
              <p className="form-info" role="status">
                Submitted. We'll contact you to connect your phone and calendar. You can still edit your profile.
              </p>
            ) : stillMissing.length === 0 ? (
              <p className="form-info">Everything needed is filled in.</p>
            ) : (
              <>
                <p>Still needed before you can submit:</p>
                <ul>
                  {stillMissing.map((item) => (
                    <li key={item}>
                      <button type="button" className="link-button" onClick={() => setStep(MISSING[item]?.step ?? 0)}>
                        {MISSING[item]?.label ?? item}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {profile.status === "draft" && (
              <div className="field check-field">
                <input
                  id="ai-disclosure"
                  type="checkbox"
                  checked={details.ai_disclosure_ack}
                  onChange={(e) => setDetails({ ai_disclosure_ack: e.target.checked })}
                />
                <label htmlFor="ai-disclosure">
                  I understand callers will be told they are speaking with an AI assistant, and that calls are processed by an AI provider.
                </label>
              </div>
            )}
          </>
        )}

        {error && (
          <p className="form-alert" role="alert">
            {error}
          </p>
        )}
        {saved && !error && (
          <p className="muted" role="status">
            {saved}
          </p>
        )}
        <div className="actions">
          {isLast ? (
            profile.status === "draft" && (
              <button type="button" className="button button-primary" disabled={busy || !canSubmit} onClick={() => void submit()}>
                {busy ? "Submitting…" : "Submit profile"}
              </button>
            )
          ) : (
            <>
              <button type="submit" className="button button-primary" disabled={busy}>
                {busy ? "Saving…" : "Save and continue"}
              </button>
              <button type="button" className="button button-secondary" disabled={busy} onClick={() => void save()}>
                Save
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}

export default function SubscriberApp() {
  const [token, setToken] = useState<string | null>(readToken);
  const [account, setAccount] = useState<Account | null>(null);
  const [options, setOptions] = useState<ProfileOptions | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    document.title = "Set up your AI receptionist";
    subscriberApi.options().then(setOptions, () => setNotice("We couldn't reach the service. Please try again later."));
  }, []);

  const signOut = useCallback((message?: string) => {
    writeToken(null);
    setToken(null);
    setAccount(null);
    setNotice(message ?? "");
  }, []);

  useEffect(() => {
    if (token && !account) {
      subscriberApi.me(token).then(setAccount, (err) => {
        if (err instanceof ApiRequestError && err.status === 401) signOut("Your session has ended. Please sign in again.");
      });
    }
  }, [token, account, signOut]);

  return (
    <main id="main" className="container staff-page">
      {notice && (
        <p className="form-alert" role="alert">
          {notice}
        </p>
      )}
      {!token ? (
        <AuthForm
          onSignedIn={(next, who) => {
            writeToken(next);
            setToken(next);
            setAccount(who);
            setNotice("");
          }}
        />
      ) : (
        options && (
          <>
            <div className="staff-header">
              <h1>Your receptionist profile</h1>
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  void subscriberApi.signOut(token).catch(() => undefined);
                  signOut();
                }}
              >
                Sign out{account ? ` ${account.full_name}` : ""}
              </button>
            </div>
            <ProfileForm token={token} options={options} onSignOut={signOut} />
          </>
        )
      )}
    </main>
  );
}
