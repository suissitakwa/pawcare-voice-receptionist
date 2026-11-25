import { request } from "./api";

export interface Option {
  value: string;
  label: string;
}

export interface ProfileOptions {
  business_types: Option[];
  /** How a home services price is meant. Missing from an older backend. */
  price_kinds?: Option[];
  voices: (Option & { sounds: "female" | "male" })[];
  calendar_providers: Option[];
  document_types: string[];
  max_document_bytes: number;
  timezones?: Option[];
  /** Price rows per business type: dog sizes for pet groomers, one row for the others. */
  sizes?: Record<string, Option[]>;
  /** The address owners share their Google Calendar with to connect it; null while not offered. */
  calendar_share_email?: string | null;
  /** Owners can sign in with Google to pick their calendar and share it in one go. */
  google_sign_in?: boolean;
}

export interface OwnedCalendar {
  id: string;
  name: string;
  primary: boolean;
}

/** The calendars the owner can pick, and a short-lived ticket to share the chosen one. */
export interface GooglePick {
  ticket: string;
  calendars: OwnedCalendar[];
}

export interface Account {
  email: string;
  full_name: string;
}

export interface SessionResult {
  token: string;
  expires_at: string;
  account: Account;
}

export interface FaqItem {
  question: string;
  answer: string;
}

export interface OpeningHours {
  /** 0 is Monday. */
  weekday: number;
  opens_at: string;
  closes_at: string;
}

export interface ServicePrice {
  size: string;
  duration_minutes: number | null;
  price_cents: number | null;
}

export interface ServiceItem {
  name: string;
  description: string;
  prices: ServicePrice[];
  /** Home services only: a fixed price, a starting price, or a fee to come out. */
  price_kind?: "fixed" | "from" | "visit_fee";
}

export interface TeamMember {
  name: string;
  /** Names of the services this person does. Empty: every service. */
  services: string[];
}

export interface ProfileDetails {
  business_phone: string | null;
  website_url: string | null;
  calendar_provider: string | null;
  calendar_other: string | null;
  /** Set by connecting, after HelloEver's account could make changes to the calendar. */
  google_calendar_id?: string | null;
  google_calendar_connected?: boolean;
  /** The connected calendar's name in Google Calendar, shown instead of its id. */
  google_calendar_name?: string | null;
  knowledge_urls: string[];
  faqs: FaqItem[];
  voice: string | null;
  ai_disclosure_ack: boolean;
  timezone?: string | null;
  address?: string | null;
  hours?: OpeningHours[];
  services?: ServiceItem[];
  team?: TeamMember[];
  /** Mobile groomers only: they go to the customer, within these ZIP codes. */
  is_mobile?: boolean;
  service_zip_codes?: string[];
  service_area?: string | null;
  /** Home services: an arrival window this many minutes long instead of exact times. */
  arrival_window_minutes?: 120 | 180 | 240 | null;
  /** Home services: texted at once when a caller has an emergency. */
  on_call_phone?: string | null;
}

export interface ProfileDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export interface Profile {
  status: "draft" | "submitted";
  business_name: string | null;
  business_type: string | null;
  details: ProfileDetails;
  documents: ProfileDocument[];
  missing: string[];
  submitted_at: string | null;
  updated_at: string;
  /** Set once the receptionist is live at /b/{business_slug}. */
  business_slug?: string | null;
}

/** A live check that the connected calendar is still shared to make changes. */
export interface CalendarCheck {
  ok: boolean;
  code: string | null;
  message: string | null;
  /** The calendar's current name, when it works. */
  name?: string | null;
}

export interface ProfileUpdate {
  business_name?: string | null;
  business_type?: string | null;
  details?: Partial<ProfileDetails>;
}

const BASE = "/api/v1/subscribers";

function json(body: unknown): RequestInit {
  return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function authed<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  return request<T>(`${BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
}

export const subscriberApi = {
  options: () => request<ProfileOptions>(`${BASE}/options`),
  signUp: (full_name: string, email: string, password: string, accept_terms: boolean) =>
    request<SessionResult>(`${BASE}/signup`, { method: "POST", ...json({ full_name, email, password, accept_terms }) }),
  signIn: (email: string, password: string) =>
    request<SessionResult>(`${BASE}/login`, { method: "POST", ...json({ email, password }) }),
  forgotPassword: (email: string) => request<unknown>(`${BASE}/password/forgot`, { method: "POST", ...json({ email }) }),
  resetPassword: (token: string, password: string) =>
    request<void>(`${BASE}/password/reset`, { method: "POST", ...json({ token, password }) }),
  me: (token: string) => authed<Account>(token, "/me"),
  signOut: (token: string) => authed<void>(token, "/logout", { method: "POST" }),
  profile: (token: string) => authed<Profile>(token, "/profile"),
  save: (token: string, update: ProfileUpdate) => authed<Profile>(token, "/profile", { method: "PATCH", ...json(update) }),
  submit: (token: string) => authed<Profile>(token, "/profile/submit", { method: "POST" }),
  upload: (token: string, file: File) =>
    authed<ProfileDocument>(token, `/profile/documents?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    }),
  connectCalendar: (token: string, calendar_id: string) =>
    authed<Profile>(token, "/profile/calendar", { method: "POST", ...json({ calendar_id }) }),
  googleStart: (token: string) => authed<{ url: string }>(token, "/profile/calendar/google/start", { method: "POST" }),
  googleCalendars: (token: string, code: string, state: string) =>
    authed<GooglePick>(token, "/profile/calendar/google/calendars", { method: "POST", ...json({ code, state }) }),
  googleConnect: (token: string, ticket: string, calendar_id: string) =>
    authed<Profile>(token, "/profile/calendar/google/connect", { method: "POST", ...json({ ticket, calendar_id }) }),
  checkCalendar: (token: string) => authed<CalendarCheck>(token, "/profile/calendar/check", { method: "POST" }),
  disconnectCalendar: (token: string) => authed<Profile>(token, "/profile/calendar", { method: "DELETE" }),
  removeDocument: (token: string, id: string) => authed<void>(token, `/profile/documents/${id}`, { method: "DELETE" }),
};

