import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import BookingForm, { longDate, todayIn, wallClock } from "./BookingForm";

const BUSINESS = {
  name: "Studio",
  tagline: "",
  about: "",
  timezone: "America/Los_Angeles",
  contact_email: "hello@pawcare.example",
  contact_phone: "",
  address: "",
  is_demo: true,
  hours: [],
};

const CATALOG = {
  size_categories: [
    { slug: "small", label: "Small", description: "Under 25 lb", requires_staff_assessment: false },
    {
      slug: "extra-large",
      label: "Extra large",
      description: "Over 90 lb. Staff assess these dogs before booking.",
      requires_staff_assessment: true,
    },
  ],
  services: [
    {
      slug: "full-groom",
      name: "Full Groom",
      description: "",
      options: [{ size: "small", duration_minutes: 90, price_cents: 7000 }],
    },
  ],
};

const SLOT = {
  starts_at: "2027-01-05T17:00:00Z",
  ends_at: "2027-01-05T18:30:00Z",
  local_start: "2027-01-05T09:00:00-08:00",
  local_end: "2027-01-05T10:30:00-08:00",
};

const SUMMARY = {
  service: "Full Groom",
  size: "Small",
  pet_name: "Biscuit",
  owner_name: "Jordan",
  owner_email: "jordan@example.com",
  owner_phone: null,
  starts_at: SLOT.starts_at,
  local_start: SLOT.local_start,
  local_end: SLOT.local_end,
  timezone: "America/Los_Angeles",
  duration_minutes: 90,
  price_cents: 7000,
};

const PREPARED = {
  confirmation_token: "token-123456789012345678901",
  summary_hash: "a".repeat(64),
  expires_at: "2027-01-04T16:10:00Z",
  summary: SUMMARY,
};

type Reply = { status: number; body: unknown } | "network";

function ok(body: unknown): Reply {
  return { status: 200, body };
}

function fail(status: number, code: string, message = "Error"): Reply {
  return { status, body: { error: { code, message } } };
}

/** Routes fetch calls by path. Each route takes a queue of replies, reusing the last. */
function mockApi(routes: Record<string, Reply[]>) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    calls.push({ path, init });
    const queue = routes[path];
    if (!queue) throw new Error(`Unexpected request to ${path}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    if (reply === "network") throw new TypeError("Failed to fetch");
    return {
      ok: reply.status < 400,
      status: reply.status,
      json: () => Promise.resolve(reply.body),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const AVAILABLE = ok({ date: "2027-01-05", timezone: "America/Los_Angeles", duration_minutes: 90, closed: false, slots: [SLOT] });

async function chooseSlot(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText("Dog size"), "small");
  fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2027-01-05" } });
  await user.click(await screen.findByRole("radio", { name: "9 AM" }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

async function fillDetails(user: ReturnType<typeof userEvent.setup>, email = "jordan@example.com") {
  await user.type(screen.getByLabelText("Your name"), "Jordan");
  await user.type(screen.getByLabelText("Your dog's name"), "Biscuit");
  if (email) await user.type(screen.getByLabelText("Email"), email);
}

afterEach(() => vi.unstubAllGlobals());

describe("BookingForm", () => {
  it("books from slot choice to reference, sending only what the customer entered", async () => {
    const calls = mockApi({
      "/api/v1/availability": [AVAILABLE],
      "/api/v1/bookings/prepare": [{ status: 201, body: PREPARED }],
      "/api/v1/bookings/confirm": [{ status: 201, body: { reference: "PC-ABCD2345", status: "confirmed", summary: SUMMARY } }],
    });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);

    await chooseSlot(user);
    await fillDetails(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));

    expect(await screen.findByRole("heading", { name: "Check your booking" })).toBeInTheDocument();
    expect(screen.getByText("$70 (demo price)")).toBeInTheDocument();
    expect(screen.getByText("9 AM to 10:30 AM")).toBeInTheDocument();
    expect(screen.getByText("Nothing is booked until you confirm. This summary is valid for 10 minutes.")).toBeInTheDocument();

    const prepareBody = JSON.parse(calls.find((c) => c.path.endsWith("prepare"))!.init!.body as string);
    expect(prepareBody).toEqual({
      service: "full-groom",
      size: "small",
      starts_at: SLOT.starts_at,
      owner_name: "Jordan",
      owner_email: "jordan@example.com",
      owner_phone: null,
      pet_name: "Biscuit",
    });

    await user.click(screen.getByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByText("PC-ABCD2345")).toBeInTheDocument();
    const confirmCall = calls.find((c) => c.path.endsWith("confirm"))!;
    expect((confirmCall.init!.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
  });

  it("nothing is booked before the customer confirms", async () => {
    const calls = mockApi({
      "/api/v1/availability": [AVAILABLE],
      "/api/v1/bookings/prepare": [{ status: 201, body: PREPARED }],
    });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await chooseSlot(user);
    await fillDetails(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));
    await screen.findByRole("heading", { name: "Check your booking" });
    expect(calls.some((c) => c.path.endsWith("confirm"))).toBe(false);
  });

  it("routes staff-assessment sizes to staff without offering times", async () => {
    const calls = mockApi({});
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await user.selectOptions(screen.getByLabelText("Dog size"), "extra-large");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2027-01-05" } });
    expect(screen.getByRole("status")).toHaveTextContent("contact staff");
    expect(calls).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("says when the business is closed", async () => {
    mockApi({ "/api/v1/availability": [ok({ date: "2027-01-11", timezone: "America/Los_Angeles", duration_minutes: 90, closed: true, slots: [] })] });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await user.selectOptions(screen.getByLabelText("Dog size"), "small");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2027-01-11" } });
    expect(await screen.findByText(/closed on Monday, January 11/)).toBeInTheDocument();
  });

  it("requires a name, dog name, and a way to reach the owner", async () => {
    const calls = mockApi({ "/api/v1/availability": [AVAILABLE] });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await chooseSlot(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));
    expect(screen.getByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByText("Enter your dog's name.")).toBeInTheDocument();
    expect(screen.getByText("Enter an email address or a phone number.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your name")).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.path.endsWith("prepare"))).toBe(false);
  });

  it("returns to the times with a clear message when the slot is taken before confirming", async () => {
    const calls = mockApi({
      "/api/v1/availability": [AVAILABLE],
      "/api/v1/bookings/prepare": [{ status: 201, body: PREPARED }],
      "/api/v1/bookings/confirm": [fail(409, "slot_unavailable")],
    });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await chooseSlot(user);
    await fillDetails(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));
    await user.click(await screen.findByRole("button", { name: "Confirm booking" }));

    expect(await screen.findByRole("heading", { name: "Choose a time" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("booked by someone else");
    await waitFor(() =>
      expect(calls.filter((c) => c.path.endsWith("availability")).length).toBeGreaterThan(1),
    );
  });

  it("retries a confirmation after a network failure with the same idempotency key", async () => {
    const calls = mockApi({
      "/api/v1/availability": [AVAILABLE],
      "/api/v1/bookings/prepare": [{ status: 201, body: PREPARED }],
      "/api/v1/bookings/confirm": [
        "network",
        { status: 200, body: { reference: "PC-RETRY234", status: "confirmed", summary: SUMMARY } },
      ],
    });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await chooseSlot(user);
    await fillDetails(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));
    await user.click(await screen.findByRole("button", { name: "Confirm booking" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Trying again is safe");
    await user.click(screen.getByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByText("PC-RETRY234")).toBeInTheDocument();

    const keys = calls
      .filter((c) => c.path.endsWith("confirm"))
      .map((c) => (c.init!.headers as Record<string, string>)["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("sends the customer back to their details when the summary expires", async () => {
    mockApi({
      "/api/v1/availability": [AVAILABLE],
      "/api/v1/bookings/prepare": [{ status: 201, body: PREPARED }],
      "/api/v1/bookings/confirm": [fail(410, "confirmation_expired")],
    });
    const user = userEvent.setup({ delay: null });
    render(<BookingForm business={BUSINESS} catalog={CATALOG} />);
    await chooseSlot(user);
    await fillDetails(user);
    await user.click(screen.getByRole("button", { name: "Review booking" }));
    await user.click(await screen.findByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByRole("heading", { name: "Your details" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("expired");
    expect(screen.getByLabelText("Your name")).toHaveValue("Jordan");
  });
});

describe("time helpers", () => {
  it("shows the business wall-clock time whatever the visitor's time zone", () => {
    expect(wallClock("2027-01-05T09:00:00-08:00")).toBe("9 AM");
    expect(wallClock("2027-01-05T16:30:00-08:00")).toBe("4:30 PM");
  });

  it("works out today in the business time zone", () => {
    // 03:00 UTC on 6 January is still 5 January in Los Angeles.
    expect(todayIn("America/Los_Angeles", new Date("2027-01-06T03:00:00Z"))).toBe("2027-01-05");
  });

  it("formats a date without shifting the day", () => {
    expect(longDate("2027-01-05")).toBe("Tuesday, January 5");
  });
});
