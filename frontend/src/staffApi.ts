import { request } from "./api";

export interface StaffUser {
  email: string;
  display_name: string;
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
}

export interface StaffBlock {
  id: string;
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_end: string;
  reason: string;
}

export interface BlockInput {
  start_date: string;
  start_time: string;
  end_date: string;
  end_time: string;
  reason: string;
}

export interface StaffMetrics {
  upcoming_bookings: number;
  bookings_last_7_days: number;
  cancellations_last_7_days: number;
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

export function login(email: string, password: string) {
  return request<{ token: string; expires_at: string; staff: StaffUser }>(
    `${BASE}/login`,
    json({ email, password }),
  );
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
  blocks: (token: string) => authed<StaffBlock[]>(token, "/blocks"),
  addBlock: (token: string, input: BlockInput) =>
    authed<{ block: StaffBlock; conflicts: StaffBooking[] }>(token, "/blocks", json(input)),
  removeBlock: (token: string, id: string) =>
    authed<void>(token, `/blocks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  metrics: (token: string) => authed<StaffMetrics>(token, "/metrics"),
};
