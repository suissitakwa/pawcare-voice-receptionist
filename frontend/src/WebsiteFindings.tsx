import { useCallback, useState, type ReactNode } from "react";
import { ApiRequestError, request } from "./api";
import { DAYS } from "./ProfileSections";
import type { OpeningHours, ProfileDetails, ServiceItem, TeamMember } from "./subscriberApi";

/** Something the website says, with the words it was taken from. */
export interface Found {
  value: string;
  quote: string;
}

export interface WebsiteDetails {
  source: string;
  pages: string[];
  business_phone: Found | null;
  address: Found | null;
  hours: (OpeningHours & { quote: string })[];
  services: (ServiceItem & { quote: string })[];
  team: { name: string; quote: string }[];
  /** Items left out because the website doesn't say them. */
  dropped: number;
}

export function readWebsite(token: string): Promise<WebsiteDetails> {
  return request<WebsiteDetails>("/api/v1/subscribers/profile/website/read", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

type Section = "phone" | "hours" | "services" | "team";

export type WebsiteState =
  | { url: string; status: "reading" }
  | { url: string; status: "failed"; message: string }
  | { url: string; status: "done"; found: WebsiteDetails; used: Section[] };

/** Reads the saved website once per address, in the background. */
export function useWebsiteFindings(token: string, onSignedOut: (err: unknown) => void) {
  const [state, setState] = useState<WebsiteState | null>(null);

  const read = useCallback(
    (url: string) => {
      setState({ url, status: "reading" });
      readWebsite(token).then(
        (found) => setState((now) => (now?.url === url ? { url, status: "done", found, used: [] } : now)),
        (err) => {
          if (err instanceof ApiRequestError && err.status === 401) onSignedOut(err);
          const message = err instanceof ApiRequestError ? err.message : "Something went wrong.";
          setState((now) => (now?.url === url ? { url, status: "failed", message } : now));
        },
      );
    },
    [token, onSignedOut],
  );

  /** After the business step is saved: read a new website while hours or services are empty. */
  const readIfUseful = useCallback(
    (details: ProfileDetails) => {
      const url = details.website_url;
      const empty = !details.hours?.length || !details.services?.length;
      if (url && empty && state?.url !== url) read(url);
    },
    [read, state],
  );

  const markUsed = (section: Section) =>
    setState((now) => (now?.status === "done" ? { ...now, used: [...now.used, section] } : now));

  return { state, read, readIfUseful, markUsed };
}

function time(value: string): string {
  const [hours, minutes] = value.split(":").map(Number);
  const suffix = hours < 12 ? "AM" : "PM";
  return `${hours % 12 || 12}${minutes ? `:${String(minutes).padStart(2, "0")}` : ""} ${suffix}`;
}

function money(cents: number | null): string {
  return cents == null ? "price not given" : `$${(cents / 100).toFixed(cents % 100 ? 2 : 0)}`;
}

function Quotes({ quotes }: { quotes: string[] }) {
  const unique = [...new Set(quotes)];
  return (
    <p className="field-hint">
      From your website: {unique.map((q) => `“${q}”`).join(" ")}
    </p>
  );
}

/**
 * What the owner's website says for the step they're on, to use or ignore. Using it only
 * changes the form; it's saved with the step like anything typed.
 */
export function WebsiteFindings({
  step,
  state,
  details,
  isMobile,
  onChange,
  onUsed,
  onRetry,
}: {
  step: string;
  state: WebsiteState | null;
  details: ProfileDetails;
  isMobile: boolean;
  onChange: (change: Partial<ProfileDetails>) => void;
  onUsed: (section: Section) => void;
  onRetry: () => void;
}) {
  if (!state || !["business", "hours", "services", "team"].includes(step)) return null;
  if (state.status === "reading") {
    if (step === "business") return null;
    return (
      <p className="muted" role="status">
        Reading your website for details to fill in…
      </p>
    );
  }
  if (state.status === "failed") {
    if (step === "business") return null;
    return (
      <div className="form-info" role="status">
        <p>We couldn't read your website: {state.message}</p>
        <button type="button" className="link-button" onClick={onRetry}>
          Try reading it again
        </button>
      </div>
    );
  }

  const { found, used } = state;
  const box = (section: Section, title: string, body: ReactNode, use: () => void, label: string) =>
    used.includes(section) ? null : (
      <div className="form-info website-findings" role="region" aria-label={title}>
        <p>
          <strong>{title}</strong>
        </p>
        {body}
        <div className="inline-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              use();
              onUsed(section);
            }}
          >
            {label}
          </button>
          <button type="button" className="link-button" onClick={() => onUsed(section)}>
            Not these
          </button>
        </div>
      </div>
    );

  if (step === "business" && found.business_phone && !details.business_phone?.trim()) {
    const phone = found.business_phone;
    return box("phone", "Phone number on your website", <p>{phone.value}</p>, () => onChange({ business_phone: phone.value }), "Use this number");
  }

  if (step === "hours" && found.hours.length > 0) {
    const address = !isMobile && !details.address?.trim() ? found.address : null;
    return box(
      "hours",
      "Opening hours on your website",
      <>
        <ul>
          {found.hours.map((h) => (
            <li key={h.weekday}>
              {DAYS[h.weekday]}: {time(h.opens_at)} to {time(h.closes_at)}
            </li>
          ))}
        </ul>
        {address && <p>Address: {address.value}</p>}
        <Quotes quotes={[...found.hours.map((h) => h.quote), ...(address ? [address.quote] : [])]} />
      </>,
      () =>
        onChange({
          hours: found.hours.map(({ weekday, opens_at, closes_at }) => ({ weekday, opens_at, closes_at })),
          ...(address ? { address: address.value } : {}),
        }),
      (details.hours ?? []).length > 0 ? "Replace with these hours" : "Use these hours",
    );
  }

  if (step === "services") {
    const have = new Set((details.services ?? []).map((s) => s.name.trim().toLowerCase()));
    const fresh = found.services.filter((s) => !have.has(s.name.toLowerCase()));
    if (fresh.length === 0) return null;
    return box(
      "services",
      "Services on your website",
      <>
        <ul>
          {fresh.map((s) => (
            <li key={s.name}>
              {s.name}
              {s.prices.length > 0 &&
                `: ${s.prices.map((p) => `${money(p.price_cents)}${p.duration_minutes ? `, ${p.duration_minutes} minutes` : ""}`).join("; ")}`}
            </li>
          ))}
        </ul>
        <Quotes quotes={fresh.map((s) => s.quote)} />
        <p className="field-hint">After adding them, fill in any blank lengths or prices: a row without both isn't saved.</p>
      </>,
      () =>
        onChange({
          services: [
            ...(details.services ?? []),
            ...fresh.map(({ name, description, prices }) => ({ name, description, prices })),
          ],
        }),
      fresh.length === 1 ? "Add this service" : `Add these ${fresh.length} services`,
    );
  }

  if (step === "team") {
    const have = new Set((details.team ?? []).map((m) => m.name.trim().toLowerCase()));
    const fresh = found.team.filter((m) => !have.has(m.name.toLowerCase()));
    if (fresh.length === 0) return null;
    return box(
      "team",
      "People on your website",
      <>
        <p>{fresh.map((m) => m.name).join(", ")}</p>
        <Quotes quotes={fresh.map((m) => m.quote)} />
      </>,
      () => onChange({ team: [...(details.team ?? []), ...fresh.map((m): TeamMember => ({ name: m.name, services: [] }))] }),
      fresh.length === 1 ? `Add ${fresh[0].name}` : "Add them to your team",
    );
  }
  return null;
}
