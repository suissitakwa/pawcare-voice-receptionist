import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import StaffApp from "./StaffApp";

const STAFF = {
  email: "maya@pawcare.example",
  display_name: "Maya",
  business_slug: "pawcare-demo",
  business_name: "PawCare Grooming Studio",
  business_is_demo: true,
};
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
  staff: "Main groomer",
};
const METRICS = {
  upcoming_bookings: 1,
  bookings_last_7_days: 1,
  cancellations_last_7_days: 0,
  voice_bookings_last_7_days: 1,
  voice_sessions_last_7_days: 3,
  phone_calls_last_7_days: 2,
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

const NO_CALENDAR = {
  connected: false,
  calendar_id: null,
  problem_code: null,
  problem: null,
  waiting: 0,
  last_synced_at: null,
  last_error: null,
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
    "GET /api/v1/staff/calls": () => ({ status: 200, body: [] }),
    "GET /api/v1/staff/webhooks": () => ({ status: 200, body: WEBHOOKS }),
    "GET /api/v1/staff/emails": () => ({
      status: 200,
      body: { address: "owner@studio.example", enabled: true, sent_last_7_days: 3, last_error: null },
    }),
    "GET /api/v1/staff/team": () => ({ status: 200, body: [{ slug: "main-groomer", name: "Main groomer" }] }),
    "GET /api/v1/staff/calendar": () => ({ status: 200, body: NO_CALENDAR }),
    "GET /api/v1/staff/plan": () => ({
      status: 200,
      body: { plan: "unlimited", state: "unlimited", price: "$49", calls_included: null, calls_this_month: 0, trial_ends_at: null, upgrade_requested_at: null },
    }),
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

  it("offers a password reset link from staff sign-in", async () => {
    const calls = mockApi(
      dashboardRoutes({ "POST /api/v1/staff/password/forgot": () => ({ status: 202, body: { status: "sent_if_account_exists" } }) }),
    );
    const user = userEvent.setup({ delay: null });
    render(<StaffApp />);
    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.type(screen.getByLabelText("Email"), "maya@pawcare.example");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If there's an account for maya@pawcare.example");
    expect(calls.some((c) => c.key === "POST /api/v1/staff/password/forgot")).toBe(true);
  });

  it("warns when a new block overlaps existing bookings", async () => {
    mockApi(
      dashboardRoutes({
        "POST /api/v1/staff/blocks": () => ({
          status: 201,
          body: {
            block: { id: "b1", starts_at: "", ends_at: "", local_start: "2027-01-05T09:00:00-08:00", local_end: "2027-01-05T12:00:00-08:00", reason: "", staff: null },
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

  it("with several staff, shows who each booking is with and blocks one person", async () => {
    const calls = mockApi(
      dashboardRoutes({
        "GET /api/v1/staff/team": () => ({
          status: 200,
          body: [
            { slug: "alex", name: "Alex" },
            { slug: "bo", name: "Bo" },
          ],
        }),
        "GET /api/v1/staff/bookings": () => ({ status: 200, body: [{ ...BOOKING, staff: "Bo" }] }),
        "POST /api/v1/staff/blocks": () => ({
          status: 201,
          body: {
            block: { id: "b1", starts_at: "", ends_at: "", local_start: "2027-01-05T09:00:00-08:00", local_end: "2027-01-05T12:00:00-08:00", reason: "", staff: "Alex" },
            conflicts: [],
          },
        }),
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    await screen.findByText("PC-ABCD2345");
    expect(screen.getByRole("columnheader", { name: "With" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Bo" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("From date"), "2027-01-05");
    await user.selectOptions(screen.getByLabelText("Who"), "alex");
    await user.click(screen.getByRole("button", { name: "Block this time" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/staff/blocks")).toBe(true));
    const sent = calls.find((c) => c.key === "POST /api/v1/staff/blocks");
    expect(JSON.parse(String(sent?.init?.body)).staff).toBe("alex");
  });

  it("shows where each booking came from, a customer's change, and who cancelled", async () => {
    mockApi(
      dashboardRoutes({
        "GET /api/v1/staff/bookings": () => ({
          status: 200,
          body: [
            { ...BOOKING, channel: "website", changed_at: "2027-01-04T18:30:00Z" },
            { ...BOOKING, reference: "PC-PHONE234", channel: "phone", status: "cancelled", cancelled_by: "customer" },
            { ...BOOKING, reference: "PC-FORM2345", source: "form", channel: "form", status: "cancelled", cancelled_by: "staff" },
          ],
        }),
      }),
    );
    render(<StaffApp />);
    await signIn();
    const changed = (await screen.findByText("PC-ABCD2345")).closest("tr")!;
    expect(changed).toHaveTextContent("Website voice");
    expect(changed).toHaveTextContent("Changed by the customer");
    const byCustomer = screen.getByText("PC-PHONE234").closest("tr")!;
    expect(byCustomer).toHaveTextContent("Phone");
    expect(byCustomer).toHaveTextContent("CancelledBy the customer");
    const byStaff = screen.getByText("PC-FORM2345").closest("tr")!;
    expect(byStaff).toHaveTextContent("Booking form");
    expect(byStaff).toHaveTextContent("By staff");
    expect(byStaff).not.toHaveTextContent("Changed by");
  });

  it("lists calls with what came of each", async () => {
    const call = {
      id: "c-1",
      started_at: "2027-01-04T17:00:00Z",
      local_start: "2027-01-04T09:00:00-08:00",
      channel: "phone",
      test_mode: false,
      in_progress: false,
      duration_seconds: 185,
      ended_by: "Caller hung up",
      booked_reference: "PC-BOOKED23",
      changed_reference: null,
      cancelled_reference: null,
      callback_requested: false,
      message_only: false,
      tool_failures: 1,
      cost_usd: 0.4213,
    };
    let refreshed = false;
    mockApi(
      dashboardRoutes({
        "GET /api/v1/staff/calls": () => ({
          status: 200,
          body: refreshed
            ? [{ ...call, id: "c-2", channel: "website", booked_reference: null, callback_requested: true, tool_failures: 0, cost_usd: null, duration_seconds: 42, ended_by: "Visitor left the page" }, call]
            : [call],
        }),
      }),
    );
    render(<StaffApp />);
    const user = await signIn();
    const section = (await screen.findByRole("heading", { name: "Calls" })).closest("section")!;
    const row = (await within(section).findByText("Booked PC-BOOKED23")).closest("tr")!;
    expect(row).toHaveTextContent("Phone");
    expect(row).toHaveTextContent("3 min 5 s");
    expect(row).toHaveTextContent("1 assistant error");
    expect(row).toHaveTextContent("Caller hung up");
    expect(row).toHaveTextContent("Cost $0.42");

    refreshed = true;
    await user.click(within(section).getByRole("button", { name: "Refresh" }));
    const web = (await within(section).findByText("Callback requested")).closest("tr")!;
    expect(web).toHaveTextContent("Website voice");
    expect(web).toHaveTextContent("42 s");
    expect(web).not.toHaveTextContent("Cost");
  });

  it("says when there are no calls yet", async () => {
    mockApi(dashboardRoutes());
    render(<StaffApp />);
    await signIn();
    expect(await screen.findByText("No calls yet.")).toBeInTheDocument();
  });

  it("says where booking and callback emails go", async () => {
    mockApi(dashboardRoutes());
    render(<StaffApp />);
    await signIn();
    expect(await screen.findByText("owner@studio.example")).toBeInTheDocument();
    expect(screen.getByText(/3 sent in the last 7 days/)).toBeInTheDocument();
  });

  it("shows whether the calendar works, and why not", async () => {
    let status: Record<string, unknown> = { ...NO_CALENDAR, connected: true, calendar_id: "owner@gmail.com", calendar_name: "Shaggy Chic bookings", waiting: 2 };
    mockApi(dashboardRoutes({ "GET /api/v1/staff/calendar": () => ({ status: 200, body: status }) }));
    render(<StaffApp />);
    const user = await signIn();
    // The calendar's name, not its id.
    expect(await screen.findByText("Shaggy Chic bookings")).toBeInTheDocument();
    expect(screen.queryByText(/owner@gmail\.com/)).not.toBeInTheDocument();
    expect(screen.getByText("Waiting to sync").nextElementSibling).toHaveTextContent("2");
    status = {
      ...status,
      problem_code: "calendar_not_shared",
      problem: "We can't see that calendar yet.",
      last_error: "saving the event returned HTTP 403",
    };
    await user.click(screen.getByRole("button", { name: "Check again" }));
    const alert = await screen.findByText(/We can't see that calendar yet/);
    expect(alert).toHaveTextContent("can't book");
    expect(screen.getByText(/HTTP 403/)).toBeInTheDocument();
  });

  it("says when no calendar is connected", async () => {
    mockApi(dashboardRoutes());
    render(<StaffApp />);
    await signIn();
    expect(await screen.findByText(/No Google Calendar is connected/)).toBeInTheDocument();
  });

  it("at a barbershop, shows clients and no dog column", async () => {
    mockApi(
      dashboardRoutes({
        "POST /api/v1/staff/login": () => ({
          status: 200,
          body: { token: "t-123", expires_at: "x", staff: { ...STAFF, business_industry: "barbershop" } },
        }),
        "GET /api/v1/staff/bookings": () => ({ status: 200, body: [{ ...BOOKING, pet_name: "", size: "Standard" }] }),
      }),
    );
    render(<StaffApp />);
    await signIn();
    await screen.findByText("PC-ABCD2345");
    expect(screen.queryByRole("columnheader", { name: "Dog" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Client" })).toBeInTheDocument();
  });

  it("with one staff member, has no staff column or choice", async () => {
    mockApi(dashboardRoutes());
    render(<StaffApp />);
    await signIn();
    await screen.findByText("PC-ABCD2345");
    expect(screen.queryByRole("columnheader", { name: "With" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Who")).not.toBeInTheDocument();
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
    expect(screen.getByText("Phone calls in the last 7 days").nextSibling).toHaveTextContent("2");
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

  describe("at a business's own address", () => {
    const REAL = {
      ...STAFF,
      email: "owner@happytails.example",
      display_name: "Sam",
      business_slug: "happy-tails",
      business_name: "Happy Tails Grooming",
      business_is_demo: false,
    };
    beforeEach(() => window.history.pushState({}, "", "/b/happy-tails/staff"));
    afterEach(() => window.history.pushState({}, "", "/"));

    it("signs in to that business and keeps its own token", async () => {
      const calls = mockApi(
        dashboardRoutes({
          "POST /api/v1/b/happy-tails/staff/login": () => ({
            status: 200,
            body: { token: "t-real", expires_at: "x", staff: REAL },
          }),
          "GET /api/v1/staff/me": () => ({ status: 200, body: REAL }),
        }),
      );
      render(<StaffApp />);
      await signIn();
      expect(await screen.findByRole("heading", { name: "Happy Tails Grooming" })).toBeInTheDocument();
      expect(calls.some((call) => call.key === "POST /api/v1/b/happy-tails/staff/login")).toBe(true);
      expect(calls.some((call) => call.key === "POST /api/v1/staff/login")).toBe(false);
      expect(sessionStorage.getItem("pawcare-staff-token:happy-tails")).toBe("t-real");
      expect(sessionStorage.getItem("pawcare-staff-token")).toBeNull();
      expect(screen.queryByText(/Demonstration dashboard/)).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to the website" })).toHaveAttribute("href", "/b/happy-tails/");
    });

    it("never shows another business's dashboard under this address", async () => {
      sessionStorage.setItem("pawcare-staff-token:happy-tails", "t-other");
      mockApi(dashboardRoutes({ "GET /api/v1/staff/me": () => ({ status: 200, body: STAFF }) }));
      render(<StaffApp />);
      expect(await screen.findByRole("heading", { name: "Staff sign in" })).toBeInTheDocument();
      expect(screen.queryByText("PC-ABCD2345")).not.toBeInTheDocument();
      expect(sessionStorage.getItem("pawcare-staff-token:happy-tails")).toBeNull();
    });
  });
});
