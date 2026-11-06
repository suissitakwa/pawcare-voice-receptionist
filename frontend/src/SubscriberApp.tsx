import { useCallback, useEffect, useId, useState } from "react";
import { ApiRequestError } from "./api";
import {
  subscriberApi,
  type Account,
  type FaqItem,
  type OpeningHours,
  type Profile,
  type ProfileDetails,
  type ProfileOptions,
  type ServiceItem,
} from "./subscriberApi";

// Session storage keeps the token for this tab only, and it is cleared when the tab closes.
const TOKEN_KEY = "receptionist-subscriber-token";
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const STEPS = [
  { id: "business", title: "Your business" },
  { id: "phone", title: "Phone, hours, and calendar" },
  { id: "services", title: "Services" },
  { id: "knowledge", title: "What the receptionist should know" },
  { id: "receptionist", title: "Your receptionist" },
  { id: "plan", title: "Notifications and plan" },
  { id: "review", title: "Review and submit" },
] as const;

// Which step fixes each item the backend reports as missing.
const MISSING: Record<string, { step: number; label: string }> = {
  business_name: { step: 0, label: "Business name" },
  business_type: { step: 0, label: "Business type" },
  timezone: { step: 0, label: "Time zone" },
  business_phone: { step: 1, label: "Business phone number" },
  hours: { step: 1, label: "Opening hours" },
  calendar_provider: { step: 1, label: "Calendar" },
  services: { step: 2, label: "At least one service" },
  knowledge: { step: 3, label: "A website, document, FAQ, or policy" },
  voice: { step: 4, label: "Voice" },
  tasks: { step: 4, label: "What the receptionist may do" },
  transfer_phone: { step: 4, label: "Number to transfer calls to" },
  notify_email: { step: 5, label: "Notification email" },
  plan: { step: 5, label: "Plan" },
  ai_disclosure_ack: { step: 5, label: "AI disclosure agreement" },
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

function orNull(value: string): string | null {
  const trimmed = value.trim();
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
  return {
    business_name: profile.business_name ?? "",
    business_type: profile.business_type ?? "",
    details: profile.details,
  };
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
  multiple,
}: {
  legend: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  multiple?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="choice-group">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="check-option">
          <input
            type={multiple ? "checkbox" : "radio"}
            name={name}
            value={option.value}
            checked={selected.includes(option.value)}
            onChange={(e) => {
              if (!multiple) onChange([option.value]);
              else onChange(e.target.checked ? [...selected, option.value] : selected.filter((v) => v !== option.value));
            }}
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

function HoursEditor({ hours, onChange }: { hours: OpeningHours[]; onChange: (hours: OpeningHours[]) => void }) {
  return (
    <fieldset className="choice-group">
      <legend>Opening hours</legend>
      <div className="hours-editor">
        {WEEKDAYS.map((day, weekday) => {
          const row = hours.find((h) => h.weekday === weekday);
          const update = (change: Partial<OpeningHours>) =>
            onChange(hours.map((h) => (h.weekday === weekday ? { ...h, ...change } : h)));
          return (
            <div key={day} className="hours-row">
              <label className="check-option">
                <input
                  type="checkbox"
                  checked={row !== undefined}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...hours, { weekday, opens: "09:00", closes: "17:00" }].sort((a, b) => a.weekday - b.weekday)
                        : hours.filter((h) => h.weekday !== weekday),
                    )
                  }
                />
                {day}
              </label>
              {row ? (
                <span className="inline-actions">
                  <input
                    type="time"
                    aria-label={`${day} opens`}
                    value={row.opens.slice(0, 5)}
                    onChange={(e) => update({ opens: e.target.value })}
                  />
                  <span aria-hidden="true">to</span>
                  <input
                    type="time"
                    aria-label={`${day} closes`}
                    value={row.closes.slice(0, 5)}
                    onChange={(e) => update({ closes: e.target.value })}
                  />
                </span>
              ) : (
                <span className="muted">Closed</span>
              )}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

function ServicesEditor({ services, onChange }: { services: ServiceItem[]; onChange: (services: ServiceItem[]) => void }) {
  const update = (index: number, change: Partial<ServiceItem>) =>
    onChange(services.map((s, i) => (i === index ? { ...s, ...change } : s)));
  return (
    <div>
      {services.map((service, index) => (
        <fieldset key={index} className="block-form">
          <legend>Service {index + 1}</legend>
          <div className="field-row">
            <Field label="Name">
              {(props) => <input {...props} value={service.name} maxLength={80} onChange={(e) => update(index, { name: e.target.value })} />}
            </Field>
            <Field label="Length in minutes" hint="Leave empty if it varies.">
              {(props) => (
                <input
                  {...props}
                  type="number"
                  min={5}
                  max={600}
                  value={service.duration_minutes ?? ""}
                  onChange={(e) => update(index, { duration_minutes: e.target.value === "" ? null : Number(e.target.value) })}
                />
              )}
            </Field>
            <Field label="Price" hint='For example "$45" or "from $60".'>
              {(props) => (
                <input {...props} value={service.price ?? ""} maxLength={40} onChange={(e) => update(index, { price: e.target.value })} />
              )}
            </Field>
          </div>
          <Field label="Description">
            {(props) => (
              <textarea
                {...props}
                rows={2}
                maxLength={300}
                value={service.description ?? ""}
                onChange={(e) => update(index, { description: e.target.value })}
              />
            )}
          </Field>
          <button type="button" className="link-button" onClick={() => onChange(services.filter((_, i) => i !== index))}>
            Remove service {index + 1}
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        className="button button-secondary"
        onClick={() => onChange([...services, { name: "", duration_minutes: null, price: null, description: null }])}
      >
        Add a service
      </button>
    </div>
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

// Empty rows and blank text become absent, so a half-filled step can still be saved.
function cleanDetails(details: ProfileDetails): ProfileDetails {
  const text = (value: string | null) => (value === null ? null : orNull(value));
  return {
    ...details,
    owner_phone: text(details.owner_phone),
    website_url: text(details.website_url),
    address: text(details.address),
    business_phone: text(details.business_phone),
    calendar_other: text(details.calendar_other),
    policies: text(details.policies),
    assistant_name: text(details.assistant_name),
    greeting: text(details.greeting),
    transfer_phone: text(details.transfer_phone),
    avoid_topics: text(details.avoid_topics),
    notify_email: text(details.notify_email),
    notify_sms: text(details.notify_sms),
    services: details.services
      .filter((s) => s.name.trim() !== "")
      .map((s) => ({ ...s, name: s.name.trim(), price: text(s.price), description: text(s.description) })),
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

  const load = useCallback(
    (next: Profile) => {
      setProfile(next);
      setDraft(toDraft(next));
      setUrls(next.details.knowledge_urls.join("\n"));
    },
    [],
  );

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

  async function save(next?: number) {
    if (!draft) return;
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const result = await subscriberApi.save(token, {
        business_name: orNull(draft.business_name),
        business_type: draft.business_type || null,
        details: cleanDetails({ ...draft.details, knowledge_urls: lines(urls) }),
      });
      load(result);
      setSaved("Saved.");
      if (next !== undefined) setStep(next);
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files || !profile) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        if (file.size > options.max_document_bytes) throw new ApiRequestError("document_too_large", `${file.name} is larger than 5 MB.`);
        await subscriberApi.upload(token, file);
      }
      const latest = await subscriberApi.profile(token);
      setProfile(latest);
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  async function removeDocument(id: string) {
    setBusy(true);
    setError("");
    try {
      await subscriberApi.removeDocument(token, id);
      setProfile(await subscriberApi.profile(token));
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      load(await subscriberApi.submit(token));
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="profile-layout">
      <nav aria-label="Profile steps">
        <ol className="step-list">
          {STEPS.map((s, index) => (
            <li key={s.id}>
              <button
                type="button"
                className="link-button"
                aria-current={index === step ? "step" : undefined}
                onClick={() => setStep(index)}
              >
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
              selected={draft.business_type ? [draft.business_type] : []}
              onChange={(values) => setDraft({ ...draft, business_type: values[0] })}
            />
            <div className="field-row">
              <Field label="Your mobile number" hint="Only for us to reach you about your account.">
                {(props) => (
                  <input {...props} type="tel" autoComplete="tel" value={details.owner_phone ?? ""} onChange={(e) => setDetails({ owner_phone: e.target.value })} />
                )}
              </Field>
              <Field label="Website">
                {(props) => (
                  <input
                    {...props}
                    type="url"
                    placeholder="https://"
                    value={details.website_url ?? ""}
                    onChange={(e) => setDetails({ website_url: e.target.value })}
                  />
                )}
              </Field>
            </div>
            <Field label="Address or service area">
              {(props) => <input {...props} maxLength={200} value={details.address ?? ""} onChange={(e) => setDetails({ address: e.target.value })} />}
            </Field>
            <Field label="Time zone">
              {(props) => (
                <select {...props} value={details.timezone ?? ""} onChange={(e) => setDetails({ timezone: e.target.value || null })}>
                  <option value="">Choose a time zone</option>
                  {options.timezones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Choices
              legend="Languages your callers speak"
              options={options.languages}
              selected={details.languages}
              onChange={(languages) => setDetails({ languages })}
              multiple
            />
          </>
        )}

        {current.id === "phone" && (
          <>
            <Field label="Business phone number" hint="The number your customers call today.">
              {(props) => (
                <input {...props} type="tel" value={details.business_phone ?? ""} onChange={(e) => setDetails({ business_phone: e.target.value })} />
              )}
            </Field>
            <Choices
              legend="How should calls reach the receptionist?"
              options={[
                { value: "forward_existing", label: "Forward my existing number when I can't answer" },
                { value: "new_number", label: "Give me a new number" },
                { value: "undecided", label: "Not sure yet" },
              ]}
              selected={details.phone_setup ? [details.phone_setup] : []}
              onChange={(values) => setDetails({ phone_setup: values[0] })}
            />
            <HoursEditor hours={details.hours} onChange={(hours) => setDetails({ hours })} />
            <Field label="Calendar you book appointments in" hint="We connect to it in a later step, so bookings never clash.">
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
                {(props) => (
                  <input {...props} maxLength={60} value={details.calendar_other ?? ""} onChange={(e) => setDetails({ calendar_other: e.target.value })} />
                )}
              </Field>
            )}
            <Field label="Minimum notice for a booking, in hours">
              {(props) => (
                <input
                  {...props}
                  type="number"
                  min={0}
                  max={168}
                  value={details.booking_notice_hours ?? ""}
                  onChange={(e) => setDetails({ booking_notice_hours: e.target.value === "" ? null : Number(e.target.value) })}
                />
              )}
            </Field>
          </>
        )}

        {current.id === "services" && (
          <>
            <p className="muted">List what customers can book, with typical length and price.</p>
            <ServicesEditor services={details.services} onChange={(services) => setDetails({ services })} />
          </>
        )}

        {current.id === "knowledge" && (
          <>
            <p className="muted">Give the receptionist at least one source. Websites and documents are read during setup.</p>
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
            <Field label="Policies" hint="Cancellation, late arrival, deposits, vaccinations, anything callers should hear.">
              {(props) => (
                <textarea {...props} rows={4} maxLength={3000} value={details.policies ?? ""} onChange={(e) => setDetails({ policies: e.target.value })} />
              )}
            </Field>
          </>
        )}

        {current.id === "receptionist" && (
          <>
            <Choices
              legend="Voice"
              options={options.voices.map((v) => ({ value: v.value, label: `${v.label} (${v.sounds === "female" ? "female" : "male"} voice)` }))}
              selected={details.voice ? [details.voice] : []}
              onChange={(values) => setDetails({ voice: values[0] })}
            />
            <div className="field-row">
              <Field label="Receptionist's name" hint='How it introduces itself, for example "Mia".'>
                {(props) => (
                  <input {...props} maxLength={40} value={details.assistant_name ?? ""} onChange={(e) => setDetails({ assistant_name: e.target.value })} />
                )}
              </Field>
              <Field label="Tone">
                {(props) => (
                  <select {...props} value={details.tone ?? ""} onChange={(e) => setDetails({ tone: e.target.value || null })}>
                    <option value="">Choose a tone</option>
                    <option value="friendly">Friendly</option>
                    <option value="professional">Professional</option>
                    <option value="calm">Calm</option>
                    <option value="upbeat">Upbeat</option>
                  </select>
                )}
              </Field>
            </div>
            <Field label="Greeting" hint="The first thing callers hear.">
              {(props) => (
                <textarea {...props} rows={2} maxLength={300} value={details.greeting ?? ""} onChange={(e) => setDetails({ greeting: e.target.value })} />
              )}
            </Field>
            <Choices legend="What may the receptionist do?" options={options.tasks} selected={details.tasks} onChange={(tasks) => setDetails({ tasks })} multiple />
            {details.tasks.includes("transfer_calls") && (
              <Field label="Number to transfer calls to">
                {(props) => (
                  <input {...props} type="tel" value={details.transfer_phone ?? ""} onChange={(e) => setDetails({ transfer_phone: e.target.value })} />
                )}
              </Field>
            )}
            <Choices
              legend="When you're closed"
              options={[
                { value: "take_message", label: "Take a message for the next day" },
                { value: "offer_booking", label: "Offer to book an appointment" },
              ]}
              selected={details.after_hours ? [details.after_hours] : []}
              onChange={(values) => setDetails({ after_hours: values[0] })}
            />
            <Field label="Topics to avoid" hint="For example medical advice, or quoting prices for coloring.">
              {(props) => (
                <textarea {...props} rows={2} maxLength={1000} value={details.avoid_topics ?? ""} onChange={(e) => setDetails({ avoid_topics: e.target.value })} />
              )}
            </Field>
          </>
        )}

        {current.id === "plan" && (
          <>
            <div className="field-row">
              <Field label="Email for new bookings and messages">
                {(props) => (
                  <input {...props} type="email" value={details.notify_email ?? ""} onChange={(e) => setDetails({ notify_email: e.target.value })} />
                )}
              </Field>
              <Field label="Text alerts to (optional)">
                {(props) => (
                  <input {...props} type="tel" value={details.notify_sms ?? ""} onChange={(e) => setDetails({ notify_sms: e.target.value })} />
                )}
              </Field>
            </div>
            <Choices
              legend="Plan"
              options={options.plans}
              selected={details.plan ? [details.plan] : []}
              onChange={(values) => setDetails({ plan: values[0] })}
            />
            <p className="field-hint">You won't be charged yet. Plans are confirmed before your receptionist goes live.</p>
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
          </>
        )}

        {current.id === "review" && (
          <>
            {profile.status === "submitted" ? (
              <p className="form-info" role="status">
                Submitted. We'll review your profile and contact you to connect your phone and calendar. You can still edit it.
              </p>
            ) : profile.missing.length === 0 ? (
              <p className="form-info">Everything needed is filled in.</p>
            ) : (
              <>
                <p>Still needed before you can submit:</p>
                <ul>
                  {profile.missing.map((item) => (
                    <li key={item}>
                      <button type="button" className="link-button" onClick={() => setStep(MISSING[item]?.step ?? 0)}>
                        {MISSING[item]?.label ?? item}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
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
              <button type="button" className="button button-primary" disabled={busy || profile.missing.length > 0} onClick={() => void submit()}>
                {busy ? "Submitting…" : "Submit profile"}
              </button>
            )
          ) : (
            <button type="submit" className="button button-primary" disabled={busy}>
              {busy ? "Saving…" : "Save and continue"}
            </button>
          )}
          {!isLast && (
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => void save()}>
              Save
            </button>
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
