import { request } from "./api";

export interface Option {
  value: string;
  label: string;
}

export interface ProfileOptions {
  business_types: Option[];
  voices: (Option & { sounds: "female" | "male" })[];
  calendar_providers: Option[];
  document_types: string[];
  max_document_bytes: number;
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

export interface ProfileDetails {
  business_phone: string | null;
  website_url: string | null;
  calendar_provider: string | null;
  calendar_other: string | null;
  knowledge_urls: string[];
  faqs: FaqItem[];
  voice: string | null;
  ai_disclosure_ack: boolean;
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
  removeDocument: (token: string, id: string) => authed<void>(token, `/profile/documents/${id}`, { method: "DELETE" }),
};

