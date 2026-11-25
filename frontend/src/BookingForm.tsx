import { useEffect, useId, useRef, useState } from "react";
import {
  ApiRequestError,
  confirmBooking,
  asksJobDetails,
  hasPets,
  fetchAvailability,
  prepareBooking,
  type Availability,
  type Business,
  type BookingConfirmation,
  type Catalog,
  type PreparedBooking,
} from "./api";
import { formatDuration, formatPriceKind, formatTime } from "./format";

type Step = "choose" | "details" | "review" | "done";

interface Contact {
  ownerName: string;
  email: string;
  phone: string;
  petName: string;
  address: string;
  job: string;
}

/** Shown by the phone field when the business texts customers. */
export const TEXT_NOTICE =
  "We'll text your confirmation and a reminder the day before. Message and data rates may apply. Reply STOP to opt out.";

const EMPTY_CONTACT: Contact = { ownerName: "", email: "", phone: "", petName: "", address: "", job: "" };
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

/** A start time, or an arrival window such as "8 AM to 11 AM". */
function slotLabel(localStart: string, arrivalUntil?: string | null): string {
  return arrivalUntil ? `${wallClock(localStart)} to ${wallClock(arrivalUntil)}` : wallClock(localStart);
}

/** "at 9 AM", or "arriving between 8 AM and 11 AM". */
function atTime(localStart: string, arrivalUntil?: string | null): string {
  return arrivalUntil
    ? `arriving between ${wallClock(localStart)} and ${wallClock(arrivalUntil)}`
    : `at ${wallClock(localStart)}`;
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

function validateContact(contact: Contact, pets: boolean, mobile = false, job = false): Record<string, string> {
  const errors: Record<string, string> = {};
  if (mobile && !contact.address.trim()) errors.address = "Enter the address where we'll come.";
  if (job && !contact.job.trim()) errors.job = "Say what needs doing.";
  if (!contact.ownerName.trim()) errors.ownerName = "Enter your name.";
  if (pets && !contact.petName.trim()) errors.petName = "Enter your dog's name.";
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
  multiline,
  ...input
}: {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  /** A few lines of text instead of one. */
  multiline?: boolean;
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
      {multiline ? (
        <textarea
          id={id}
          rows={3}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          {...(input as unknown as React.TextareaHTMLAttributes<HTMLTextAreaElement>)}
        />
      ) : (
        <input
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          {...input}
        />
      )}
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
  const pets = hasPets(business);
  const asksJob = asksJobDetails(business);
  // Salons and barbershops book every service as their one size, without asking.
  const [size, setSize] = useState(pets ? "" : (catalog.size_categories[0]?.slug ?? ""));
  // Empty: anyone who does the service.
  const [staff, setStaff] = useState("");
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
  const selectedSlot = availability?.slots.find((s) => s.starts_at === startsAt);
  const option = selectedService?.options.find((o) => o.size === size);
  const team = catalog.staff ?? [];
  const teamForService = team.filter((member) => member.services.includes(service));

  // Moving to a new step moves focus there, for keyboard and screen reader users. Not on the
  // first render, or the page would jump down to the form as soon as it opens.
  const shownStep = useRef(step);
  useEffect(() => {
    if (shownStep.current === step) return;
    shownStep.current = step;
    headingRef.current?.focus();
  }, [step]);

  // A person who doesn't do the newly chosen service can't stay selected.
  useEffect(() => {
    if (staff && !teamForService.some((member) => member.slug === staff)) setStaff("");
  }, [service, staff, teamForService]);

  useEffect(() => {
    setStartsAt("");
    setAvailability(null);
    if (!service || !size || !date || needsAssessment) {
      setSlotsState("idle");
      return;
    }
    const controller = new AbortController();
    setSlotsState("loading");
    fetchAvailability(service, size, date, controller.signal, staff)
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
  }, [service, size, date, staff, needsAssessment, reload]);

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
    const errors = validateContact(contact, pets, business.is_mobile ?? false, asksJob);
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
        ...(staff ? { staff } : {}),
        ...(business.is_mobile ? { service_address: contact.address.trim() } : {}),
        ...(asksJob ? { job_details: contact.job.trim() } : {}),
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
          {summary.service}
          {summary.pet_name ? ` for ${summary.pet_name}` : ""}
          {summary.staff ? ` with ${summary.staff}` : ""} on{" "}
          {longDate(summary.local_start.slice(0, 10))} {atTime(summary.local_start, summary.arrival_until)}.
        </p>
        {business.texts_customers && summary.owner_phone && (
          <p>
            We're texting the details to {summary.owner_phone}, and we'll send a reminder the day
            before.
          </p>
        )}
        <p className="muted">
          {business.is_demo
            ? "This is a demonstration, so no appointment will take place and nothing will be sent to you. Keep the reference if you'd like to mention it to staff."
            : "Keep the reference in case you need to change or cancel the appointment."}
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
      ...((summary.pet_name
        ? [
            ["Service", `${summary.service}, ${summary.size.toLowerCase()} dog`],
            ["Dog", summary.pet_name],
          ]
        : [["Service", summary.service]]) as [string, string][]),
      ["Date", longDate(summary.local_start.slice(0, 10))],
      summary.arrival_until
        ? ["Arrival", `Between ${wallClock(summary.local_start)} and ${wallClock(summary.arrival_until)}`]
        : ["Time", `${wallClock(summary.local_start)} to ${wallClock(summary.local_end)}`],
      ...(summary.staff ? ([["With", summary.staff]] as [string, string][]) : []),
      ["Length", formatDuration(summary.duration_minutes)],
      ["Price", `${formatPriceKind(summary.price_cents, summary.price_kind)}${business.is_demo ? " (demo price)" : ""}`],
      ...((summary.service_address ? [["Address", summary.service_address]] : []) as [string, string][]),
      ...((summary.job_details ? [["What needs doing", summary.job_details]] : []) as [string, string][]),
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
          {selectedService?.name} on {longDate(date)}{" "}
          {atTime(
            selectedSlot?.local_start ?? `${date}T00:00:00`,
            selectedSlot?.arrival_until,
          )}
          .
          {business.is_demo && "This is a demo, so please use made-up details."}
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
        {business.is_mobile && (
          <Field
            id={`${id}-address`}
            label="Address where we'll come"
            autoComplete="street-address"
            hint={`Street, city and ZIP code.${business.service_area ? ` We cover ${business.service_area}.` : ""}`}
            value={contact.address}
            error={fieldErrors.address}
            maxLength={200}
            onChange={(e) => setContact({ ...contact, address: e.target.value })}
          />
        )}
        {asksJob && (
          <Field
            id={`${id}-job`}
            label="What needs doing?"
            hint="For example: the kitchen sink is leaking under the cabinet since this morning."
            multiline
            value={contact.job}
            error={fieldErrors.job}
            maxLength={500}
            onChange={(e) => setContact({ ...contact, job: e.target.value })}
          />
        )}
        {pets && (
          <Field
            id={`${id}-pet`}
            label="Your dog's name"
            autoComplete="off"
            value={contact.petName}
            error={fieldErrors.petName}
            maxLength={40}
            onChange={(e) => setContact({ ...contact, petName: e.target.value })}
          />
        )}
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
          hint={business.texts_customers ? TEXT_NOTICE : undefined}
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
        {pets && (
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
        )}
        {team.length > 1 && (
          <div className="field">
            <label htmlFor={`${id}-staff`}>Who would you like?</label>
            <select id={`${id}-staff`} value={staff} onChange={(e) => setStaff(e.target.value)}>
              <option value="">Anyone available</option>
              {teamForService.map((member) => (
                <option key={member.slug} value={member.slug}>
                  {member.name}
                </option>
              ))}
            </select>
          </div>
        )}
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
          {formatPriceKind(option.price_cents, selectedService?.price_kind)}
          {business.is_demo ? " demo price." : "."}
          {selectedService?.price_kind === "visit_fee" && " The technician quotes any repair on site."}
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
          <legend>
            {business.arrival_window_minutes ? "Arrival windows" : "Open times"} on {longDate(date)}
          </legend>
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
                <span>{slotLabel(slot.local_start, slot.arrival_until)}</span>
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
