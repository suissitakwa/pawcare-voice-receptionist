const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface BusinessHours {
  weekday: number;
  opens_at: string;
  closes_at: string;
}

export interface Business {
  name: string;
  tagline: string;
  about: string;
  timezone: string;
  contact_email: string;
  contact_phone: string;
  address: string;
  is_demo: boolean;
  hours: BusinessHours[];
}

export interface SizeCategory {
  slug: string;
  label: string;
  description: string;
  requires_staff_assessment: boolean;
}

export interface ServiceOption {
  size: string;
  duration_minutes: number;
  price_cents: number;
}

export interface Service {
  slug: string;
  name: string;
  description: string;
  options: ServiceOption[];
}

export interface Catalog {
  size_categories: SizeCategory[];
  services: Service[];
}

export interface Faq {
  slug: string;
  question: string;
  answer: string;
}

export interface SiteData {
  business: Business;
  catalog: Catalog;
  faqs: Faq[];
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

export async function fetchSiteData(signal?: AbortSignal): Promise<SiteData> {
  const [business, catalog, faqs] = await Promise.all([
    getJson<Business>("/api/v1/business", signal),
    getJson<Catalog>("/api/v1/services", signal),
    getJson<Faq[]>("/api/v1/faqs", signal),
  ]);
  // A misconfigured host can answer unknown paths with a 200 HTML page or other shapes.
  if (!Array.isArray(catalog.services) || !Array.isArray(catalog.size_categories) || !Array.isArray(faqs)) {
    throw new Error("Unexpected response shape from the API");
  }
  return { business, catalog, faqs };
}

export interface Slot {
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_end: string;
}

export interface Availability {
  date: string;
  timezone: string;
  duration_minutes: number;
  closed: boolean;
  slots: Slot[];
}

export interface BookingDetails {
  service: string;
  size: string;
  starts_at: string;
  owner_name: string;
  owner_email: string | null;
  owner_phone: string | null;
  pet_name: string;
}

export interface BookingSummary {
  service: string;
  size: string;
  pet_name: string;
  owner_name: string;
  owner_email: string | null;
  owner_phone: string | null;
  starts_at: string;
  local_start: string;
  local_end: string;
  timezone: string;
  duration_minutes: number;
  price_cents: number;
}

export interface PreparedBooking {
  confirmation_token: string;
  summary_hash: string;
  expires_at: string;
  summary: BookingSummary;
}

export interface BookingConfirmation {
  reference: string;
  status: string;
  summary: BookingSummary;
}

/** A failure the UI can act on. `code` is the API's error code, or "network" if no reply came. */
export class ApiRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 0,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiRequestError("network", "We couldn't reach the booking service.");
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.error;
    throw new ApiRequestError(
      typeof detail?.code === "string" ? detail.code : "unexpected",
      typeof detail?.message === "string" ? detail.message : "Something went wrong.",
      response.status,
    );
  }
  return body as T;
}

export function fetchAvailability(
  service: string,
  size: string,
  date: string,
  signal?: AbortSignal,
): Promise<Availability> {
  const query = new URLSearchParams({ service, size, date });
  return request<Availability>(`/api/v1/availability?${query}`, { signal });
}

export function prepareBooking(details: BookingDetails): Promise<PreparedBooking> {
  return request<PreparedBooking>("/api/v1/bookings/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(details),
  });
}

export function confirmBooking(
  prepared: PreparedBooking,
  idempotencyKey: string,
): Promise<BookingConfirmation> {
  return request<BookingConfirmation>("/api/v1/bookings/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({
      confirmation_token: prepared.confirmation_token,
      summary_hash: prepared.summary_hash,
    }),
  });
}

export interface VoiceSession {
  session_id: string;
  session_token: string;
  mode: "test" | "live";
  client_secret: string | null;
  expires_at: string | null;
  max_duration_seconds: number;
}

export type VoiceEndReason =
  | "user_ended"
  | "timeout"
  | "error"
  | "disconnected"
  | "mic_denied"
  | "page_closed";

export interface VoiceUsage {
  input_tokens: number;
  output_tokens: number;
  responses: number;
  incomplete_responses: number;
  interrupted_responses: number;
}

export function startVoiceSession(): Promise<VoiceSession> {
  return request<VoiceSession>("/api/v1/voice/sessions", { method: "POST" });
}

/** Best effort: `keepalive` lets the report finish even while the page is closing. */
export function endVoiceSession(
  session: VoiceSession,
  reason: VoiceEndReason,
  usage: VoiceUsage | null,
): Promise<void> {
  return request<void>(`/api/v1/voice/sessions/${session.session_id}/end`, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_token: session.session_token, reason, usage }),
  }).catch(() => undefined);
}

export interface VoiceToolResult {
  ok: boolean;
  result: Record<string, unknown>;
  prepared: PreparedBooking | null;
}

export function callVoiceTool(session: VoiceSession, name: string, args: string): Promise<VoiceToolResult> {
  return request<VoiceToolResult>(
    `/api/v1/voice/sessions/${session.session_id}/tools/${encodeURIComponent(name)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_token: session.session_token, arguments: args }),
    },
  );
}
