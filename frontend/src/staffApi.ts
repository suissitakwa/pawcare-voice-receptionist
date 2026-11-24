import { request } from "./api";
import { businessSlug } from "./site";

export interface StaffUser {
  email: string;
  display_name: string;
  business_slug: string;
  business_name: string;
  business_is_demo: boolean;
  business_industry?: string;
}

export interface StaffBooking {
  reference: string;
  status: string;
  service: string;
  size: string;
  pet_name: string;
  owner_name: string;
  owner_email: string | null;
  owner_phone: string | null;
  starts_at: string;
  local_start: string;
  local_end: string;
  price_cents: number;
  created_at: string;
  cancelled_at: string | null;
  source: "form" | "voice";
  /** Where it was booked. Missing from an older backend. */
  channel?: "form" | "phone" | "website";
  /** When the customer last changed it by phone or on the website. */
  changed_at?: string | null;
  cancelled_by?: "staff" | "customer" | null;
  staff: string;
  /** Where the appointment is, at a mobile business. */
  service_address?: string | null;
}

/** One conversation with the receptionist, by phone or on the website. */
export interface StaffCall {
  id: string;
  started_at: string;
  local_start: string;
  channel: "phone" | "website";
  test_mode: boolean;
  in_progress: boolean;
  duration_seconds: number | null;
  ended_by: string | null;
  booked_reference: string | null;
  changed_reference: string | null;
  cancelled_reference: string | null;
  callback_requested: boolean;
  message_only: boolean;
  tool_failures: number;
  cost_usd: number | null;
}

export interface StaffCallback {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  reason: string;
  note: string;
  status: "open" | "done";
  created_at: string;
  resolved_at: string | null;
}

export interface StaffBlock {
  id: string;
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_end: string;
  reason: string;
  /** null: the whole business. */
  staff: string | null;
}

export interface TeamMember {
  slug: string;
  name: string;
}

export interface BlockInput {
  start_date: string;
  start_time: string;
  end_date: string;
  end_time: string;
  reason: string;
  /** A staff member's slug; empty blocks the whole business. */
  staff?: string;
}

export interface StaffMetrics {
  upcoming_bookings: number;
  bookings_last_7_days: number;
  cancellations_last_7_days: number;
  voice_bookings_last_7_days: number;
  voice_sessions_last_7_days: number;
  phone_calls_last_7_days: number;
  open_callbacks: number;
  tool_failures_last_7_days: number;
  voice_replies_measured: number;
  voice_reply_median_ms: number | null;
  voice_reply_p90_ms: number | null;
}

const BASE = "/api/v1/staff";

function authed<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  return request<T>(`${BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Signs in to this page's business: /b/{slug}/staff signs in there, /staff to the demo. */
export function login(email: string, password: string, slug: string | null = businessSlug()) {
  return request<{ token: string; expires_at: string; staff: StaffUser }>(
    slug ? `/api/v1/b/${slug}/staff/login` : `${BASE}/login`,
    json({ email, password }),
  );
}

/** "Forgot password?" for this page's business. */
export function forgotPassword(email: string, slug: string | null = businessSlug()) {
  return request<unknown>(slug ? `/api/v1/b/${slug}/staff/password/forgot` : `${BASE}/password/forgot`, json({ email }));
}

export function resetPassword(token: string, password: string) {
  return request<void>(`${BASE}/password/reset`, json({ token, password }));
}

export interface WebhookStatus {
  configured: boolean;
  problem: string | null;
  pending: number;
  delivered_last_7_days: number;
  failed_last_7_days: number;
  last_delivered_at: string | null;
  last_error: string | null;
}

/** The free trial or plan, and this month's calls against its allowance. */
export interface PlanStatus {
  plan: "trial" | "starter" | "unlimited";
  state: "trial" | "trial_ended" | "active" | "payment_stopped" | "allowance_used" | "unlimited";
  price: string;
  calls_included: number | null;
  calls_this_month: number;
  trial_ends_at: string | null;
  upgrade_requested_at: string | null;
  /** The card subscription's status in Stripe (active, past_due, canceled, ...), or null. */
  billing_status: string | null;
  /** When a card subscription the owner cancelled stops; null while it renews. */
  billing_ends_at: string | null;
  /** Card payment through Stripe is set up; otherwise owners ask for an invoice. */
  card_payment: boolean;
  /** The business pays through Stripe, so its billing portal can open. */
  manage_billing: boolean;
}

export interface CalendarStatus {
  connected: boolean;
  calendar_id: string | null;
  /** Its name in Google Calendar, when the live check could read it. */
  calendar_name?: string | null;
  /** From a live check with Google; null when bookings can be read and written. */
  problem_code: string | null;
  problem: string | null;
  /** Bookings still to be written to or removed from the calendar. */
  waiting: number;
  last_synced_at: string | null;
  last_error: string | null;
}

export interface EmailStatus {
  /** Where emails about bookings and callback requests go; null when none are sent. */
  address: string | null;
  enabled: boolean;
  sent_last_7_days: number;
  last_error: string | null;
}

export const staffApi = {
  logout: (token: string) => authed<void>(token, "/logout", { method: "POST" }),
  me: (token: string) => authed<StaffUser>(token, "/me"),
  bookings: (token: string, scope: "upcoming" | "past") =>
    authed<StaffBooking[]>(token, `/bookings?scope=${scope}`),
  cancel: (token: string, reference: string) =>
    authed<StaffBooking>(token, `/bookings/${encodeURIComponent(reference)}/cancel`, {
      method: "POST",
    }),
  team: (token: string) => authed<TeamMember[]>(token, "/team"),
  blocks: (token: string) => authed<StaffBlock[]>(token, "/blocks"),
  addBlock: (token: string, input: BlockInput) =>
    authed<{ block: StaffBlock; conflicts: StaffBooking[] }>(token, "/blocks", json(input)),
  removeBlock: (token: string, id: string) =>
    authed<void>(token, `/blocks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  metrics: (token: string) => authed<StaffMetrics>(token, "/metrics"),
  callbacks: (token: string) => authed<StaffCallback[]>(token, "/callbacks?status=open"),
  calls: (token: string) => authed<StaffCall[]>(token, "/calls"),
  completeCallback: (token: string, id: string) =>
    authed<StaffCallback>(token, `/callbacks/${encodeURIComponent(id)}/done`, { method: "POST" }),
  webhooks: (token: string) => authed<WebhookStatus>(token, "/webhooks"),
  emails: (token: string) => authed<EmailStatus>(token, "/emails"),
  calendar: (token: string) => authed<CalendarStatus>(token, "/calendar"),
  plan: (token: string) => authed<PlanStatus>(token, "/plan"),
  requestPlan: (token: string) => authed<PlanStatus>(token, "/plan/request", { method: "POST" }),
  /** A Stripe Checkout page for the paid plan. */
  startCheckout: (token: string) => authed<{ url: string }>(token, "/plan/checkout", { method: "POST" }),
  /** Stripe's customer portal: the card, invoices, cancelling. */
  billingPortal: (token: string) => authed<{ url: string }>(token, "/plan/portal", { method: "POST" }),
  sendTestEvent: (token: string) => authed<{ event_id: string }>(token, "/webhooks/test", { method: "POST" }),
};
