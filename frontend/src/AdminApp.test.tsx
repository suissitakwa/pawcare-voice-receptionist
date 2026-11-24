import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AdminApp from "./AdminApp";

const ADMIN = { email: "ops@example.com", display_name: "Ops" };
const ACCOUNT = {
  id: "a1",
  email: "sam@example.com",
  full_name: "Sam Rivera",
  created_at: "2027-01-04T16:00:00Z",
  last_sign_in_at: "2027-01-05T16:00:00Z",
  locked: true,
  profile_status: "submitted",
  business_name: "Shaggy Chic",
  business_slug: "shaggy-chic",
  plan: "trial",
};
const DETAIL = {
  ...ACCOUNT,
  business_type: "pet_grooming",
  submitted_at: "2027-01-04T17:00:00Z",
  business_is_protected: false,
  trial_ends_at: "2027-01-18T16:00:00Z",
  staff_logins: [{ email: "sam@example.com", display_name: "Sam" }],
  phone_numbers: ["+14155550199"],
  bookings: 3,
  calls: 5,
};

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
    "POST /api/v1/admin/login": () => ({ status: 200, body: { token: "adm-1", expires_at: "x", admin: ADMIN } }),
    "GET /api/v1/admin/accounts": () => ({ status: 200, body: [ACCOUNT] }),
    "GET /api/v1/admin/actions": () => ({ status: 200, body: [] }),
    "GET /api/v1/admin/accounts/a1": () => ({ status: 200, body: DETAIL }),
    ...overrides,
  };
}

async function signIn() {
  const user = userEvent.setup({ delay: null });
  await user.type(screen.getByLabelText("Email"), ADMIN.email);
  await user.type(screen.getByLabelText("Password"), "admin password long enough");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  return user;
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("AdminApp", () => {
  it("shows the server's error for a wrong password", async () => {
    mockApi({
      "POST /api/v1/admin/login": () => ({
        status: 401,
        body: { error: { code: "invalid_credentials", message: "That email and password do not match." } },
      }),
    });
    render(<AdminApp />);
    await signIn();
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
    expect(sessionStorage.length).toBe(0);
  });

  it("lists sign-ups and unlocks one", async () => {
    const calls = mockApi(
      routes({ "POST /api/v1/admin/accounts/a1/unlock": () => ({ status: 200, body: { ...DETAIL, locked: false } }) }),
    );
    render(<AdminApp />);
    const user = await signIn();
    const row = (await screen.findByText("sam@example.com")).closest("tr")!;
    expect(within(row).getByText("Live")).toBeInTheDocument();
    expect(within(row).getByText("Locked")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Manage sam@example.com" }));
    expect(await screen.findByText("+14155550199")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Account unlocked.");
    expect(calls.some((c) => c.key === "POST /api/v1/admin/accounts/a1/unlock")).toBe(true);
    expect(calls.every((c) => c.key.includes("login") || new Headers(c.init?.headers).get("Authorization") === "Bearer adm-1")).toBe(true);
  });

  it("deletes only after the email is typed, with the business by default", async () => {
    const calls = mockApi(
      routes({
        "DELETE /api/v1/admin/accounts/a1": () => ({
          status: 200,
          body: { deleted_email: "sam@example.com", deleted_business_slug: "shaggy-chic", deleted_bookings: 3, released_phone_numbers: 1 },
        }),
      }),
    );
    render(<AdminApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Manage sam@example.com" }));
    const remove = await screen.findByRole("button", { name: "Delete account" });
    expect(remove).toBeDisabled();
    await user.type(screen.getByLabelText("Type sam@example.com to confirm"), "sam@example.com");
    expect(remove).toBeEnabled();
    await user.click(remove);
    expect(await screen.findByRole("status")).toHaveTextContent("Deleted sam@example.com and its business shaggy-chic");
    const sent = calls.find((c) => c.key === "DELETE /api/v1/admin/accounts/a1");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ confirm_email: "sam@example.com", delete_business: true });
  });

  it("changes a business's plan", async () => {
    const calls = mockApi(
      routes({
        "PATCH /api/v1/admin/businesses/shaggy-chic/plan": () => ({ status: 200, body: { ...DETAIL, plan: "starter", trial_ends_at: null } }),
      }),
    );
    render(<AdminApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Manage sam@example.com" }));
    await user.selectOptions(await screen.findByLabelText("Plan"), "starter");
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plan set to Starter.");
    const sent = calls.find((c) => c.key.startsWith("PATCH"));
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ plan: "starter" });
  });
});

const WAITING = { ...DETAIL, locked: false, business_slug: null, plan: null, trial_ends_at: null, staff_logins: [], phone_numbers: [], bookings: 0, calls: 0 };
const REVIEW = {
  business_name: "Shaggy Chic",
  business_slug: "shaggy-chic",
  timezone: "America/Los_Angeles",
  hours: ["Tue 09:00-17:00"],
  services: [{ name: "Full Groom", options: ["small 90 min $65.00"] }],
  team: ["Sam"],
  faqs: 1,
  notes: [],
  blocking: [],
};

describe("AdminApp review", () => {
  it("shows only sign-ups awaiting review when asked", async () => {
    const calls = mockApi(routes({ "GET /api/v1/admin/accounts": () => ({ status: 200, body: [{ ...ACCOUNT, business_slug: null, plan: null }] }) }));
    render(<AdminApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Awaiting review" }));
    expect(await screen.findByRole("heading", { name: /Awaiting review \(1\)/ })).toBeInTheDocument();
    expect(calls.some((c) => c.key === "GET /api/v1/admin/accounts")).toBe(true);
    const fetched = (vi.mocked(fetch).mock.calls as [string][]).map(([url]) => url);
    expect(fetched.some((url) => url.endsWith("/api/v1/admin/accounts?stage=awaiting_review"))).toBe(true);
  });

  it("previews the business and approves it, then lists what's left", async () => {
    const calls = mockApi(
      routes({
        "GET /api/v1/admin/accounts/a1": () => ({ status: 200, body: WAITING }),
        "GET /api/v1/admin/accounts/a1/review": () => ({ status: 200, body: REVIEW }),
        "POST /api/v1/admin/accounts/a1/approve": () => ({
          status: 200,
          body: { business_slug: "shaggy-chic", next_steps: ["Buy their Twilio number and register it."], account: DETAIL },
        }),
      }),
    );
    render(<AdminApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Manage sam@example.com" }));
    expect(await screen.findByText("Full Groom")).toBeInTheDocument();
    expect(screen.getByText("Tue 09:00-17:00")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve and go live" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Shaggy Chic is live at /b/shaggy-chic");
    expect(screen.getByText("Buy their Twilio number and register it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and go live" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/accounts/a1/approve")).toHaveLength(1);
  });

  it("won't approve a profile with something missing", async () => {
    mockApi(
      routes({
        "GET /api/v1/admin/accounts/a1": () => ({ status: 200, body: WAITING }),
        "GET /api/v1/admin/accounts/a1/review": () => ({
          status: 200,
          body: { ...REVIEW, blocking: ["No services and prices in the profile."] },
        }),
      }),
    );
    render(<AdminApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Manage sam@example.com" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No services and prices");
    expect(screen.getByRole("button", { name: "Approve and go live" })).toBeDisabled();
  });
});
