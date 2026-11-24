import { request } from "./api";

export interface AdminUser {
  email: string;
  display_name: string;
}

export type Plan = "trial" | "starter" | "unlimited";

/** One sign-up, as the admin list shows it. */
export interface AdminAccount {
  id: string;
  email: string;
  full_name: string;
  created_at: string;
  last_sign_in_at: string | null;
  locked: boolean;
  profile_status: string | null;
  business_name: string | null;
  /** Set once the profile has become a live business. */
  business_slug: string | null;
  plan: Plan | null;
}

export interface AdminAccountDetail extends AdminAccount {
  business_type: string | null;
  submitted_at: string | null;
  /** The demo: deleting the account can't take it down. */
  business_is_protected: boolean;
  trial_ends_at: string | null;
  staff_logins: { email: string; display_name: string }[];
  phone_numbers: string[];
  bookings: number;
  calls: number;
}

export interface DeletionResult {
  deleted_email: string;
  deleted_business_slug: string | null;
  deleted_bookings: number;
  released_phone_numbers: number;
}

export interface AdminAction {
  admin_email: string;
  action: string;
  target: string;
  details: Record<string, unknown>;
  created_at: string;
}

const BASE = "/api/v1/admin";

function authed<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  return request<T>(`${BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

const account = (id: string) => `/accounts/${encodeURIComponent(id)}`;

export function adminLogin(email: string, password: string) {
  return request<{ token: string; expires_at: string; admin: AdminUser }>(
    `${BASE}/login`,
    json("POST", { email, password }),
  );
}

export const adminApi = {
  logout: (token: string) => authed<void>(token, "/logout", { method: "POST" }),
  me: (token: string) => authed<AdminUser>(token, "/me"),
  accounts: (token: string, q = "") =>
    authed<AdminAccount[]>(token, `/accounts${q ? `?${new URLSearchParams({ q })}` : ""}`),
  account: (token: string, id: string) => authed<AdminAccountDetail>(token, account(id)),
  update: (token: string, id: string, changes: { full_name?: string; email?: string }) =>
    authed<AdminAccountDetail>(token, account(id), json("PATCH", changes)),
  unlock: (token: string, id: string) =>
    authed<AdminAccountDetail>(token, `${account(id)}/unlock`, { method: "POST" }),
  signOut: (token: string, id: string) =>
    authed<AdminAccountDetail>(token, `${account(id)}/sign-out`, { method: "POST" }),
  remove: (token: string, id: string, confirmEmail: string, deleteBusiness: boolean) =>
    authed<DeletionResult>(
      token,
      account(id),
      json("DELETE", { confirm_email: confirmEmail, delete_business: deleteBusiness }),
    ),
  setPlan: (token: string, slug: string, plan: Plan, trialDays?: number) =>
    authed<AdminAccountDetail | null>(
      token,
      `/businesses/${encodeURIComponent(slug)}/plan`,
      json("PATCH", { plan, ...(plan === "trial" && trialDays ? { trial_days: trialDays } : {}) }),
    ),
  actions: (token: string) => authed<AdminAction[]>(token, "/actions"),
};
