import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import StaffApp from "./StaffApp";

const STAFF = { email: "maya@pawcare.example", display_name: "Maya" };
const BOOKING = {
  reference: "PC-ABCD2345",
  status: "confirmed",
  service: "Full Groom",
  size: "Small",
  pet_name: "Biscuit",
  owner_name: "Jordan",
  owner_email: "jordan@example.com",
  owner_phone: null,
  starts_at: "2027-01-05T17:00:00Z",
  local_start: "2027-01-05T09:00:00-08:00",
  local_end: "2027-01-05T10:30:00-08:00",
  price_cents: 7000,
  created_at: "2027-01-04T16:00:00Z",
  cancelled_at: null,
  source: "voice",
};
const METRICS = {
  upcoming_bookings: 1,
  bookings_last_7_days: 1,
  cancellations_last_7_days: 0,
  voice_bookings_last_7_days: 1,
  voice_sessions_last_7_days: 3,
  open_callbacks: 1,
  tool_failures_last_7_days: 0,
  voice_replies_measured: 12,
  voice_reply_median_ms: 1240,
  voice_reply_p90_ms: 2100,
};
const WEBHOOKS = {
  configured: true,
  problem: null,
  pending: 0,
  delivered_last_7_days: 4,
  failed_last_7_days: 1,
  last_delivered_at: "2027-01-04T16:00:00Z",
  last_error: "HTTP 503",
};
const CALLBACK = {
  id: "c1",
  name: "Sam",
  phone: "(323) 555-0101",
  email: null,
  reason: "health",
  note: "Skin rash",
  status: "open",
  created_at: "2027-01-04T18:00:00Z",
  resolved_at: null,
};

type Reply = { status: number; body?: unknown };
type Route = (init?: RequestInit) => Reply;

function mockApi(routes: Record<string, Route>) {
  const calls: { key: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const key = `${init?.method ?? "GET"} ${parsed.pathname}`;
      calls.push({ key, init });
      const route = routes[key];
      if (!route) throw new Error(`Unexpected ${key}`);
      const reply = route(init);
      return { ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body) };
    }),
  );
  return calls;
}

function dashboardRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "POST /api/v1/staff/login": () => ({ status: 200, body: { token: "t-123", expires_at: "x", staff: STAFF } }),
    "GET /api/v1/staff/me": () => ({ status: 200, body: STAFF }),
    "GET /api/v1/staff/bookings": () => ({ status: 200, body: [BOOKING] }),
    "GET /api/v1/staff/blocks": () => ({ status: 200, body: [] }),
    "GET /api/v1/staff/metrics": () => ({ status: 200, body: METRICS }),
    "GET /api/v1/staff/callbacks": () => ({ status: 200, body: [CALLBACK] }),
    "GET /api/v1/staff/webhooks": () => ({ status: 200, body: WEBHOOKS }),
    ...overrides,
  };
}

async function signIn() {
  const user = userEvent.setup({ delay: null });
  await user.type(screen.getByLabelText("Email"), "maya@pawcare.example");
  await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  return user;
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("StaffApp", () => {
  it("shows the server's error for a wrong password and stores nothing", async () => {
    mockApi({
      "POST /api/v1/staff/login": () => ({
        status: 401,
        body: { error: { code: "invalid_credentials", message: "That email and password do not match." } },
      }),
    });
    render(<StaffApp />);
    await signIn();
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
    expect(sessionStorage.getItem("pawcare-staff-token")).toBeNull();
  });

  it("signs in and shows metrics and bookings, sending the token on every request", async () => {
    const calls = mockApi(dashboardRoutes());
    render(<StaffApp />);
    await signIn();
    expect(await screen.findByText("Signed in as Maya", { exact: false })).toBeInTheDocument();
    expect(await screen.findByText("PC-ABCD2345")).toBeInTheDocument();
    expect(screen.getByText("Upcoming bookings").nextSibling).toHaveTextContent("1");
    expect(screen.getByText("jordan@example.com")).toBeInTheDocument();
    expect(sessionStorage.getItem("pawcare-staff-token")).toBe("t-123");
    const authed = calls.filter((c) => c.key !== "POST /api/v1/staff/login");
    expect(authed.length).toBeGreaterThan(0);
    for (const call of authed) {
      expect((call.init?.headers as Record<string, string>).Authorization).toBe("Bearer t-123");
    }
  });

  it("cancels only after a second, explicit click", async () => {
    let cancelled = false;
    const calls = mockApi(
      dashboardRoutes({
        "POST /api/v1/staff/bookings/PC-ABCD2345/cancel": () => {
          cancelled = true;
          return { status: 200, body: { ...BOOKING, status: "cancelled" } };
        },
        "GET /api/v1/staff/bookings": () => ({
          status: 200,
          body: [cancelled ? { ...BOOKING, status: "cancelled" } : BOOKING],
        }),
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.key.endsWith("/cancel"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Yes, cancel PC-ABCD2345" }));
    expect(await screen.findByRole("status")).toHaveTextContent("PC-ABCD2345 was cancelled");
    await waitFor(() => expect(screen.getByText("Cancelled")).toBeInTheDocument());
  });

  it("returns to sign in when the session has ended", async () => {
    sessionStorage.setItem("pawcare-staff-token", "expired");
    mockApi({
      "GET /api/v1/staff/me": () => ({
        status: 401,
        body: { error: { code: "not_authenticated", message: "Please sign in." } },
      }),
    });
    render(<StaffApp />);
    expect(await screen.findByRole("heading", { name: "Staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem("pawcare-staff-token")).toBeNull();
  });

  it("warns when a new block overlaps existing bookings", async () => {
    mockApi(
      dashboardRoutes({
        "POST /api/v1/staff/blocks": () => ({
          status: 201,
          body: {
            block: { id: "b1", starts_at: "", ends_at: "", local_start: "2027-01-05T09:00:00-08:00", local_end: "2027-01-05T12:00:00-08:00", reason: "" },
            conflicts: [BOOKING],
          },
        }),
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    await screen.findByText("PC-ABCD2345");
    const from = screen.getByLabelText("From date");
    await user.type(from, "2027-01-05");
    await user.click(screen.getByRole("button", { name: "Block this time" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("1 existing booking overlaps it: PC-ABCD2345");
  });

  it("shows callback requests and voice figures, and marks a callback done", async () => {
    let done = false;
    const calls = mockApi(
      dashboardRoutes({
        "GET /api/v1/staff/callbacks": () => ({ status: 200, body: done ? [] : [CALLBACK] }),
        "POST /api/v1/staff/callbacks/c1/done": () => {
          done = true;
          return { status: 200, body: { ...CALLBACK, status: "done" } };
        },
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    expect(await screen.findByText("Sam")).toBeInTheDocument();
    expect(screen.getByText(/Health question: Skin rash/)).toBeInTheDocument();
    expect(screen.getByText("Voice calls in the last 7 days").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("Voice reply time, median (90th percentile)").nextSibling).toHaveTextContent(
      "1.2 s (2.1 s), 12 replies",
    );
    expect(screen.getByText("Voice")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mark done" }));
    expect(await screen.findByText("No open callback requests.")).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/staff/callbacks/c1/done")).toBe(true);
  });

  it("resumes a stored session without asking to sign in again", async () => {
    sessionStorage.setItem("pawcare-staff-token", "t-123");
    mockApi(dashboardRoutes());
    render(<StaffApp />);
    expect(await screen.findByText("PC-ABCD2345")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Staff sign in" })).not.toBeInTheDocument();
  });

  it("shows automation delivery and queues a test event", async () => {
    let queued = false;
    mockApi(
      dashboardRoutes({
        "POST /api/v1/staff/webhooks/test": () => {
          queued = true;
          return { status: 202, body: { event_id: "e1" } };
        },
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Sent in the last 7 days").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("Last error: HTTP 503")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send test event" }));
    expect(await screen.findByText(/Test event queued/)).toBeInTheDocument();
    expect(queued).toBe(true);
  });
});
