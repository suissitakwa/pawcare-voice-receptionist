import { useEffect, useId, useRef, useState } from "react";
import {
  ApiRequestError,
  confirmBooking,
  fetchAvailability,
  prepareBooking,
  type Availability,
  type Business,
  type BookingConfirmation,
  type Catalog,
  type PreparedBooking,
} from "./api";
import { formatDuration, formatPrice, formatTime } from "./format";

type Step = "choose" | "details" | "review" | "done";

interface Contact {
  ownerName: string;
  email: string;
  phone: string;
  petName: string;
}

const EMPTY_CONTACT: Contact = { ownerName: "", email: "", phone: "", petName: "" };
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9 ().-]{7,20}$/;

/** Today's date in the business time zone, as YYYY-MM-DD. */
export function todayIn(timezone: string, now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Wall-clock label from an offset timestamp, without converting to the visitor's zone. */
export function wallClock(localTimestamp: string): string {
  return formatTime(localTimestamp.slice(11, 19));
}

export function longDate(isoDate: string): string {
  return new Date(`${isoDate}T12:00:00Z`).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function validateContact(contact: Contact): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!contact.ownerName.trim()) errors.ownerName = "Enter your name.";
  if (!contact.petName.trim()) errors.petName = "Enter your dog's name.";
  const email = contact.email.trim();
  const phone = contact.phone.trim();
  if (email && !EMAIL_PATTERN.test(email)) errors.email = "Enter a valid email address.";
  if (phone && !PHONE_PATTERN.test(phone)) errors.phone = "Enter a valid phone number.";
  if (!email && !phone) errors.email = "Enter an email address or a phone number.";
  return errors;
}

function Field({
  id,
  label,
  error,
  hint,
  ...input
}: {
  id: string;
  label: string;
  error?: string;
  hint?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {hint && (
        <p id={`${id}-hint`} className="field-hint">
          {hint}
        </p>
      )}
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        {...input}
      />
      {error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

export default function BookingForm({ business, catalog }: { business: Business; catalog: Catalog }) {
  const id = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const today = todayIn(business.timezone);

  const [step, setStep] = useState<Step>("choose");
  const [service, setService] = useState(catalog.services[0]?.slug ?? "");
  const [size, setSize] = useState("");
  const [date, setDate] = useState("");
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [slotsState, setSlotsState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [startsAt, setStartsAt] = useState("");
  const [contact, setContact] = useState<Contact>(EMPTY_CONTACT);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [prepared, setPrepared] = useState<PreparedBooking | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirmation, setConfirmation] = useState<BookingConfirmation | null>(null);
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  const sizeCategory = catalog.size_categories.find((s) => s.slug === size);
  const needsAssessment = sizeCategory?.requires_staff_assessment ?? false;
  const selectedService = catalog.services.find((s) => s.slug === service);
  const option = selectedService?.options.find((o) => o.size === size);

  useEffect(() => {
    headingRef.current?.focus();
  }, [step]);

  useEffect(() => {
    setStartsAt("");
    setAvailability(null);
    if (!service || !size || !date || needsAssessment) {
      setSlotsState("idle");
      return;
    }
    const controller = new AbortController();
    setSlotsState("loading");
    fetchAvailability(service, size, date, controller.signal)
      .then((result) => {
        setAvailability(result);
        setSlotsState("ready");
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setSlotsState("error");
        if (error instanceof ApiRequestError && error.code !== "network") {
          setMessage({ tone: "error", text: error.message });
        }
      });
    return () => controller.abort();
  }, [service, size, date, needsAssessment, reload]);

  function startOver(text?: string) {
    setPrepared(null);
    setIdempotencyKey("");
    setStartsAt("");
    setReload((value) => value + 1);
    setStep("choose");
    setMessage(text ? { tone: "error", text } : null);
  }

  async function review(event: React.FormEvent) {
    event.preventDefault();
    const errors = validateContact(contact);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setMessage({ tone: "error", text: "Please fix the highlighted fields." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await prepareBooking({
        service,
        size,
        starts_at: startsAt,
        owner_name: contact.ownerName.trim(),
        owner_email: contact.email.trim() || null,
        owner_phone: contact.phone.trim() || null,
        pet_name: contact.petName.trim(),
      });
      setPrepared(result);
      // One key per summary: retrying this summary can never create a second booking.
      setIdempotencyKey(newIdempotencyKey());
      setStep("review");
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === "slot_unavailable") {
        startOver("That time was just taken. Please choose another.");
      } else {
        setMessage({
          tone: "error",
          text: error instanceof ApiRequestError ? error.message : "Something went wrong.",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!prepared) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await confirmBooking(prepared, idempotencyKey);
      setConfirmation(result);
      setStep("done");
    } catch (error) {
      const code = error instanceof ApiRequestError ? error.code : "unexpected";
      if (code === "slot_unavailable") {
        startOver("Sorry, that time was booked by someone else. Please choose another.");
      } else if (code === "confirmation_expired") {
        setStep("details");
        setMessage({
          tone: "error",
          text: "The summary expired. Review your details again to get a fresh summary.",
        });
      } else if (code === "network") {
        setMessage({
          tone: "error",
          text: "We couldn't confirm whether your booking went through. Trying again is safe and won't book twice.",
        });
      } else {
        setMessage({
          tone: "error",
          text: error instanceof ApiRequestError ? error.message : "Something went wrong.",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  const status = message && (
    <p className={message.tone === "error" ? "form-alert" : "form-info"} role="alert">
      {message.text}
    </p>
  );

  if (step === "done" && confirmation) {
    const summary = confirmation.summary;
    return (
      <div className="booking-panel booking-done">
        <h3 ref={headingRef} tabIndex={-1}>
          You're booked
        </h3>
        <p className="reference">
          Reference <strong>{confirmation.reference}</strong>
        </p>
        <p>
          {summary.service} for {summary.pet_name} on {longDate(summary.local_start.slice(0, 10))}{" "}
          at {wallClock(summary.local_start)}.
        </p>
        <p className="muted">
          This is a demonstration, so no appointment will take place and nothing will be sent to
          you. Keep the reference if you'd like to mention it to staff.
        </p>
        <button
          type="button"
          className="button button-secondary"
          onClick={() => {
            setConfirmation(null);
            setContact(EMPTY_CONTACT);
            startOver();
          }}
        >
          Book another appointment
        </button>
      </div>
    );
  }

  if (step === "review" && prepared) {
    const summary = prepared.summary;
    const rows: [string, string][] = [
      ["Service", `${summary.service}, ${summary.size.toLowerCase()} dog`],
      ["Dog", summary.pet_name],
      ["Date", longDate(summary.local_start.slice(0, 10))],
      ["Time", `${wallClock(summary.local_start)} to ${wallClock(summary.local_end)}`],
      ["Length", formatDuration(summary.duration_minutes)],
      ["Price", `${formatPrice(summary.price_cents)} (demo price)`],
      ["Name", summary.owner_name],
      ["Contact", [summary.owner_email, summary.owner_phone].filter(Boolean).join(", ")],
    ];
    return (
      <div className="booking-panel">
        <h3 ref={headingRef} tabIndex={-1}>
          Check your booking
        </h3>
        <dl className="summary-list">
          {rows.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <p className="muted">
          Nothing is booked until you confirm. This summary is valid for 10 minutes.
        </p>
        {status}
        <div className="actions">
          <button type="button" className="button button-primary" onClick={confirm} disabled={busy}>
            {busy ? "Confirming…" : "Confirm booking"}
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setPrepared(null);
              setStep("details");
            }}
            disabled={busy}
          >
            Change details
          </button>
        </div>
      </div>
    );
  }

  if (step === "details") {
    return (
      <form className="booking-panel" onSubmit={review} noValidate>
        <h3 ref={headingRef} tabIndex={-1}>
          Your details
        </h3>
        <p className="muted">
          {selectedService?.name} on {longDate(date)} at {wallClock(availability?.slots.find((s) => s.starts_at === startsAt)?.local_start ?? `${date}T00:00:00`)}.
          This is a demo, so please use made-up details.
        </p>
        <Field
          id={`${id}-owner`}
          label="Your name"
          autoComplete="off"
          value={contact.ownerName}
          error={fieldErrors.ownerName}
          maxLength={80}
          onChange={(e) => setContact({ ...contact, ownerName: e.target.value })}
        />
        <Field
          id={`${id}-pet`}
          label="Your dog's name"
          autoComplete="off"
          value={contact.petName}
          error={fieldErrors.petName}
          maxLength={40}
          onChange={(e) => setContact({ ...contact, petName: e.target.value })}
        />
        <Field
          id={`${id}-email`}
          label="Email"
          type="email"
          autoComplete="off"
          hint="Give an email address, a phone number, or both."
          value={contact.email}
          error={fieldErrors.email}
          maxLength={254}
          onChange={(e) => setContact({ ...contact, email: e.target.value })}
        />
        <Field
          id={`${id}-phone`}
          label="Phone"
          type="tel"
          autoComplete="off"
          value={contact.phone}
          error={fieldErrors.phone}
          maxLength={20}
          onChange={(e) => setContact({ ...contact, phone: e.target.value })}
        />
        {status}
        <div className="actions">
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? "Checking…" : "Review booking"}
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setStep("choose")}
            disabled={busy}
          >
            Back
          </button>
        </div>
      </form>
    );
  }

  return (
    <form
      className="booking-panel"
      onSubmit={(event) => {
        event.preventDefault();
        if (startsAt) {
          setMessage(null);
          setStep("details");
        } else {
          setMessage({ tone: "error", text: "Choose a time to continue." });
        }
      }}
    >
      <h3 ref={headingRef} tabIndex={-1}>
        Choose a time
      </h3>
      <fieldset>
        <legend>Service</legend>
        <div className="choice-grid">
          {catalog.services.map((item) => (
            <label key={item.slug} className="choice">
              <input
                type="radio"
                name={`${id}-service`}
                value={item.slug}
                checked={service === item.slug}
                onChange={() => setService(item.slug)}
              />
              <span>{item.name}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="field-row">
        <div className="field">
          <label htmlFor={`${id}-size`}>Dog size</label>
          <select id={`${id}-size`} value={size} onChange={(e) => setSize(e.target.value)}>
            <option value="">Choose a size</option>
            {catalog.size_categories.map((item) => (
              <option key={item.slug} value={item.slug}>
                {item.label}, {item.description.split(".")[0].toLowerCase()}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-date`}>Date</label>
          <input
            id={`${id}-date`}
            type="date"
            min={today}
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
      </div>
      {needsAssessment && (
        <p className="form-info" role="status">
          {sizeCategory?.description} Please contact staff using the details on this page, so
          they can check your dog's needs first.
        </p>
      )}
      {option && !needsAssessment && (
        <p className="muted">
          {selectedService?.name}: {formatDuration(option.duration_minutes)},{" "}
          {formatPrice(option.price_cents)} demo price.
        </p>
      )}
      <div aria-live="polite">
        {slotsState === "loading" && <p className="muted">Checking open times…</p>}
        {slotsState === "error" && !message && (
          <p className="form-alert" role="alert">
            Open times could not be loaded.{" "}
            <button type="button" className="link-button" onClick={() => setReload((v) => v + 1)}>
              Try again
            </button>
          </p>
        )}
        {slotsState === "ready" && availability?.closed && (
          <p className="muted">We're closed on {longDate(date)}. Please choose another date.</p>
        )}
        {slotsState === "ready" && availability && !availability.closed && availability.slots.length === 0 && (
          <p className="muted">{longDate(date)} is fully booked. Please choose another date.</p>
        )}
      </div>
      {slotsState === "ready" && availability && availability.slots.length > 0 && (
        <fieldset>
          <legend>Open times on {longDate(date)}</legend>
          <div className="slot-grid">
            {availability.slots.map((slot) => (
              <label key={slot.starts_at} className="slot">
                <input
                  type="radio"
                  name={`${id}-slot`}
                  value={slot.starts_at}
                  checked={startsAt === slot.starts_at}
                  onChange={() => setStartsAt(slot.starts_at)}
                />
                <span>{wallClock(slot.local_start)}</span>
              </label>
            ))}
          </div>
          <p className="muted">Times are in {business.timezone.replace("_", " ")} time.</p>
        </fieldset>
      )}
      {status}
      <div className="actions">
        <button type="submit" className="button button-primary" disabled={!startsAt}>
          Continue
        </button>
      </div>
    </form>
  );
}
