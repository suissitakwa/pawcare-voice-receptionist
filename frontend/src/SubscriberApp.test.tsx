import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubscriberApp from "./SubscriberApp";

const OPTIONS = {
  business_types: [
    { value: "pet_grooming", label: "Pet grooming" },
    { value: "hair_salon", label: "Hair salon" },
    { value: "barbershop", label: "Barbershop" },
  ],
  voices: [
    { value: "marin", label: "Marin", sounds: "female" },
    { value: "cedar", label: "Cedar", sounds: "male" },
  ],
  calendar_providers: [{ value: "google", label: "Google Calendar" }],
  languages: [{ value: "en", label: "English" }],
  tasks: [{ value: "answer_questions", label: "Answer questions" }],
  plans: [{ value: "starter", label: "Starter" }],
  timezones: ["America/Los_Angeles"],
  document_types: ["application/pdf"],
  max_document_bytes: 5 * 1024 * 1024,
};
const EMPTY_DETAILS = {
  owner_phone: null,
  website_url: null,
  address: null,
  timezone: null,
  languages: [],
  business_phone: null,
  phone_setup: null,
  hours: [],
  calendar_provider: null,
  calendar_other: null,
  booking_notice_hours: null,
  services: [],
  knowledge_urls: [],
  faqs: [],
  policies: null,
  voice: null,
  assistant_name: null,
  greeting: null,
  tone: null,
  tasks: [],
  transfer_phone: null,
  after_hours: null,
  avoid_topics: null,
  notify_email: null,
  notify_sms: null,
  plan: null,
  ai_disclosure_ack: false,
};
const PROFILE = {
  status: "draft",
  business_name: null,
  business_type: null,
  details: EMPTY_DETAILS,
  documents: [],
  missing: ["business_name", "voice"],
  submitted_at: null,
  updated_at: "2027-01-04T16:00:00Z",
};
const ACCOUNT = { email: "sam@example.com", full_name: "Sam Rivera" };

type Reply = { status: number; body?: unknown };
type Route = (init?: RequestInit) => Reply;

function mockApi(routes: Record<string, Route>) {
  const calls: { key: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${new URL(url).pathname}`;
      calls.push({ key, init });
      const route = routes[key];
      if (!route) throw new Error(`Unexpected ${key}`);
      const reply = route(init);
      return { ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body) };
    }),
  );
  return calls;
}

function routes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "GET /api/v1/subscribers/options": () => ({ status: 200, body: OPTIONS }),
    "POST /api/v1/subscribers/signup": () => ({ status: 201, body: { token: "s-1", expires_at: "x", account: ACCOUNT } }),
    "GET /api/v1/subscribers/me": () => ({ status: 200, body: ACCOUNT }),
    "GET /api/v1/subscribers/profile": () => ({ status: 200, body: PROFILE }),
    ...overrides,
  };
}

async function signUp() {
  const user = userEvent.setup({ delay: null });
  await user.type(screen.getByLabelText("Your name"), "Sam Rivera");
  await user.type(screen.getByLabelText("Email"), "sam@example.com");
  await user.type(screen.getByLabelText("Password"), "correct horse battery");
  await user.click(screen.getByLabelText(/I accept the terms/));
  await user.click(screen.getByRole("button", { name: "Create account" }));
  return user;
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("SubscriberApp", () => {
  it("creates an account and opens the first profile step", async () => {
    const calls = mockApi(routes());
    render(<SubscriberApp />);
    await signUp();
    expect(await screen.findByRole("heading", { name: "Your business" })).toBeInTheDocument();
    expect(sessionStorage.getItem("receptionist-subscriber-token")).toBe("s-1");
    const signup = calls.find((c) => c.key === "POST /api/v1/subscribers/signup");
    expect(JSON.parse(String(signup?.init?.body))).toEqual({
      full_name: "Sam Rivera",
      email: "sam@example.com",
      password: "correct horse battery",
      accept_terms: true,
    });
  });

  it("shows the server's reason when the email is already registered", async () => {
    mockApi(
      routes({
        "POST /api/v1/subscribers/signup": () => ({
          status: 409,
          body: { error: { code: "email_taken", message: "An account with that email already exists. Please sign in." } },
        }),
      }),
    );
    render(<SubscriberApp />);
    await signUp();
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(sessionStorage.getItem("receptionist-subscriber-token")).toBeNull();
  });

  it("saves a step with the token, turning blank fields into nothing, and moves on", async () => {
    let saved: Record<string, unknown> = {};
    const calls = mockApi(
      routes({
        "PATCH /api/v1/subscribers/profile": (init) => {
          saved = JSON.parse(String(init?.body));
          return { status: 200, body: { ...PROFILE, business_name: "Fade Factory", business_type: "barbershop" } };
        },
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.type(await screen.findByLabelText("Business name"), "Fade Factory");
    await user.click(screen.getByLabelText("Barbershop"));
    await user.selectOptions(screen.getByLabelText("Time zone"), "America/Los_Angeles");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(await screen.findByRole("heading", { name: "Phone, hours, and calendar" })).toBeInTheDocument();
    expect(saved.business_name).toBe("Fade Factory");
    expect(saved.business_type).toBe("barbershop");
    const details = saved.details as Record<string, unknown>;
    expect(details.timezone).toBe("America/Los_Angeles");
    expect(details.website_url).toBeNull();
    const patch = calls.find((c) => c.key === "PATCH /api/v1/subscribers/profile");
    expect((patch?.init?.headers as Record<string, string>).Authorization).toBe("Bearer s-1");
  });

  it("offers female and male voices and sends the chosen one", async () => {
    let saved: Record<string, unknown> = {};
    mockApi(
      routes({
        "PATCH /api/v1/subscribers/profile": (init) => {
          saved = JSON.parse(String(init?.body));
          return { status: 200, body: PROFILE };
        },
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Your receptionist" }));
    const voices = screen.getByRole("group", { name: "Voice" });
    expect(within(voices).getByLabelText("Marin (female voice)")).toBeInTheDocument();
    await user.click(within(voices).getByLabelText("Cedar (male voice)"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect((saved.details as Record<string, unknown>).voice).toBe("cedar");
  });

  it("lists what is missing on the review step and keeps submit disabled", async () => {
    mockApi(routes());
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Review and submit" }));
    expect(screen.getByRole("button", { name: "Business name" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit profile" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Voice" }));
    expect(screen.getByRole("heading", { name: "Your receptionist" })).toBeInTheDocument();
  });

  it("uploads a document as the raw file with its type", async () => {
    const calls = mockApi(
      routes({
        "POST /api/v1/subscribers/profile/documents": () => ({
          status: 201,
          body: { id: "d1", filename: "prices.pdf", content_type: "application/pdf", size_bytes: 2048, created_at: "x" },
        }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "What the receptionist should know" }));
    const file = new File(["%PDF-1.7"], "prices.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Upload documents"), file);
    await vi.waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/subscribers/profile/documents")).toBe(true));
    const upload = calls.find((c) => c.key === "POST /api/v1/subscribers/profile/documents");
    expect(upload?.init?.body).toBe(file);
    expect((upload?.init?.headers as Record<string, string>)["Content-Type"]).toBe("application/pdf");
  });

  it("returns to sign-in when the session has expired", async () => {
    sessionStorage.setItem("receptionist-subscriber-token", "old");
    const expired = () => ({ status: 401, body: { error: { code: "not_authenticated", message: "Please sign in." } } });
    mockApi(routes({ "GET /api/v1/subscribers/me": expired, "GET /api/v1/subscribers/profile": expired }));
    render(<SubscriberApp />);
    expect(await screen.findByRole("alert")).toHaveTextContent("session has ended");
    expect(screen.getByRole("button", { name: "Create account" })).toBeInTheDocument();
    expect(sessionStorage.getItem("receptionist-subscriber-token")).toBeNull();
  });
});
