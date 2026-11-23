import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubscriberApp, { navigation } from "./SubscriberApp";

const OPTIONS = {
  business_types: [
    { value: "pet_grooming", label: "Pet grooming" },
    { value: "hair_salon", label: "Hair salon" },
    { value: "barbershop", label: "Barbershop" },
  ],
  voices: [
    { value: "marin", label: "Female voice", sounds: "female" },
    { value: "cedar", label: "Male voice", sounds: "male" },
  ],
  calendar_providers: [{ value: "google", label: "Google Calendar" }],
  document_types: ["application/pdf"],
  max_document_bytes: 5 * 1024 * 1024,
  timezones: [{ value: "America/Los_Angeles", label: "Pacific" }],
  sizes: {
    pet_grooming: [
      { value: "small", label: "Small dog" },
      { value: "medium", label: "Medium dog" },
      { value: "large", label: "Large dog" },
    ],
    barbershop: [{ value: "any", label: "Any" }],
  },
};
const EMPTY_DETAILS = {
  business_phone: null,
  website_url: null,
  calendar_provider: null,
  calendar_other: null,
  knowledge_urls: [],
  faqs: [],
  voice: null,
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

  it("tells the owner when their receptionist is live, with its links", async () => {
    mockApi(
      routes({
        "GET /api/v1/subscribers/profile": () => ({
          status: 200,
          body: { ...PROFILE, status: "submitted", business_name: "Shaggy Chic", missing: [], business_slug: "shaggy-chic" },
        }),
      }),
    );
    render(<SubscriberApp />);
    await signUp();
    expect(await screen.findByText("Your receptionist is live.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /\/b\/shaggy-chic$/ })).toHaveAttribute("href", "/b/shaggy-chic");
    expect(screen.getByRole("link", { name: "your staff dashboard" })).toHaveAttribute("href", "/b/shaggy-chic/staff");
  });

  it("shows no live notice before the receptionist is set up", async () => {
    mockApi(routes());
    render(<SubscriberApp />);
    await signUp();
    await screen.findByRole("heading", { name: "Your business" });
    expect(screen.queryByText("Your receptionist is live.")).not.toBeInTheDocument();
  });

  it("links the terms and privacy policy from the sign-up checkbox", () => {
    mockApi(routes());
    render(<SubscriberApp />);
    expect(screen.getByText(/then \$49 a month for up to 100 calls\. No card/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "terms of service" })).toHaveAttribute("href", "https://helloeverai.com/terms/");
    expect(screen.getByRole("link", { name: "privacy policy" })).toHaveAttribute("href", "https://helloeverai.com/privacy/");
  });

  it("sends a reset link from the sign-in form, answering the same for any email", async () => {
    const calls = mockApi(
      routes({ "POST /api/v1/subscribers/password/forgot": () => ({ status: 202, body: { status: "sent_if_account_exists" } }) }),
    );
    const user = userEvent.setup({ delay: null });
    render(<SubscriberApp />);
    await user.click(screen.getByRole("button", { name: "I already have an account" }));
    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.type(screen.getByLabelText("Email"), "sam@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If there's an account for sam@example.com");
    const sent = calls.find((c) => c.key === "POST /api/v1/subscribers/password/forgot");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ email: "sam@example.com" });
  });

  it("sets a new password from the emailed link and clears the link from the address", async () => {
    window.history.pushState({}, "", "/get-started?reset=tok-1234567890abcdefghij");
    const calls = mockApi(routes({ "POST /api/v1/subscribers/password/reset": () => ({ status: 204 }) }));
    const user = userEvent.setup({ delay: null });
    render(<SubscriberApp />);
    expect(window.location.search).toBe("");
    await user.type(screen.getByLabelText("New password"), "a-brand-new-password");
    await user.type(screen.getByLabelText("New password again"), "a-different-password");
    await user.click(screen.getByRole("button", { name: "Set new password" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The two passwords don't match.");
    await user.clear(screen.getByLabelText("New password again"));
    await user.type(screen.getByLabelText("New password again"), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByRole("heading", { name: "Password changed" })).toBeInTheDocument();
    const sent = calls.find((c) => c.key === "POST /api/v1/subscribers/password/reset");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ token: "tok-1234567890abcdefghij", password: "a-brand-new-password" });
    window.history.pushState({}, "", "/");
  });

  it("explains an expired reset link", async () => {
    window.history.pushState({}, "", "/get-started?reset=tok-expired-1234567890");
    mockApi(
      routes({
        "POST /api/v1/subscribers/password/reset": () => ({
          status: 400,
          body: { error: { code: "reset_link_invalid", message: "This link has expired or was already used." } },
        }),
      }),
    );
    const user = userEvent.setup({ delay: null });
    render(<SubscriberApp />);
    await user.type(screen.getByLabelText("New password"), "a-brand-new-password");
    await user.type(screen.getByLabelText("New password again"), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This link has expired or was already used.");
    window.history.pushState({}, "", "/");
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
    await user.type(screen.getByLabelText("Business phone number"), "  (415) 555-0134 ");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(await screen.findByRole("heading", { name: "Where and when" })).toBeInTheDocument();
    expect(saved.business_name).toBe("Fade Factory");
    expect(saved.business_type).toBe("barbershop");
    const details = saved.details as Record<string, unknown>;
    expect(details.business_phone).toBe("(415) 555-0134");
    expect(details.website_url).toBeNull();
    const patch = calls.find((c) => c.key === "PATCH /api/v1/subscribers/profile");
    expect((patch?.init?.headers as Record<string, string>).Authorization).toBe("Bearer s-1");
  });

  it("collects hours, services with prices per dog size, and the team", async () => {
    const bodies: Record<string, unknown>[] = [];
    mockApi(
      routes({
        "PATCH /api/v1/subscribers/profile": (init) => {
          bodies.push(JSON.parse(String(init?.body)));
          return { status: 200, body: { ...PROFILE, business_type: "pet_grooming" } };
        },
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();

    await user.click(await screen.findByRole("button", { name: "Where and when" }));
    await user.selectOptions(screen.getByLabelText("Time zone"), "America/Los_Angeles");
    await user.click(screen.getByRole("checkbox", { name: "Tuesday" }));
    await user.click(screen.getByRole("button", { name: "Save and continue" }));
    let details = bodies.at(-1)!.details as Record<string, unknown>;
    expect(details.timezone).toBe("America/Los_Angeles");
    expect(details.hours).toEqual([{ weekday: 1, opens_at: "09:00", closes_at: "17:00" }]);

    expect(await screen.findByRole("heading", { name: "Services and prices" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add a service" }));
    await user.type(screen.getByLabelText("Service name"), "Full Groom");
    await user.type(screen.getByLabelText("Full Groom, Small dog, minutes"), "90");
    await user.type(screen.getByLabelText("Full Groom, Small dog, price in dollars"), "65.50");
    // A half-filled row isn't sent.
    await user.type(screen.getByLabelText("Full Groom, Large dog, minutes"), "150");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));
    details = bodies.at(-1)!.details as Record<string, unknown>;
    expect(details.services).toEqual([
      { name: "Full Groom", description: "", prices: [{ size: "small", duration_minutes: 90, price_cents: 6550 }] },
    ]);
  });

  it("connects a Google Calendar shared with HelloEver's account", async () => {
    let connected: Record<string, unknown> = {};
    const connectedProfile = {
      ...PROFILE,
      details: { ...EMPTY_DETAILS, calendar_provider: "google", google_calendar_id: "sam@gmail.com", google_calendar_connected: true, google_calendar_name: "Sam Rivera" },
    };
    const calls = mockApi(
      routes({
        "GET /api/v1/subscribers/options": () => ({
          status: 200,
          body: { ...OPTIONS, calendar_share_email: "bookings@helloever.iam.gserviceaccount.com" },
        }),
        "PATCH /api/v1/subscribers/profile": () => ({ status: 200, body: PROFILE }),
        "POST /api/v1/subscribers/profile/calendar": (init) => {
          connected = JSON.parse(String(init?.body));
          return { status: 200, body: connectedProfile };
        },
        "DELETE /api/v1/subscribers/profile/calendar": () => ({ status: 200, body: PROFILE }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Your calendar" }));
    await user.selectOptions(screen.getByLabelText("Calendar you book appointments in"), "google");
    expect(screen.getByText("bookings@helloever.iam.gserviceaccount.com")).toBeInTheDocument();
    const connect = screen.getByRole("button", { name: "Connect calendar" });
    expect(connect).toBeDisabled();
    await user.type(screen.getByLabelText("Calendar ID"), " sam@gmail.com ");
    await user.click(connect);
    expect(await screen.findByText("Your calendar is connected.")).toBeInTheDocument();
    expect(connected).toEqual({ calendar_id: "sam@gmail.com" });
    // Unsaved edits are saved before connecting.
    const keys = calls.map((c) => c.key);
    expect(keys.indexOf("PATCH /api/v1/subscribers/profile")).toBeLessThan(keys.indexOf("POST /api/v1/subscribers/profile/calendar"));
    expect(screen.getByText("Sam Rivera", { selector: ".calendar-connect strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Your calendar is disconnected.")).toBeInTheDocument();
  });

  it("explains a calendar that isn't shared yet", async () => {
    mockApi(
      routes({
        "GET /api/v1/subscribers/options": () => ({ status: 200, body: { ...OPTIONS, calendar_share_email: "bot@x.iam" } }),
        "PATCH /api/v1/subscribers/profile": () => ({ status: 200, body: PROFILE }),
        "POST /api/v1/subscribers/profile/calendar": () => ({
          status: 422,
          body: { error: { code: "calendar_not_shared", message: "We can't see that calendar yet. Share it with bot@x.iam." } },
        }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Your calendar" }));
    await user.selectOptions(screen.getByLabelText("Calendar you book appointments in"), "google");
    await user.type(screen.getByLabelText("Calendar ID"), "sam@gmail.com");
    await user.click(screen.getByRole("button", { name: "Connect calendar" }));
    expect(await screen.findByText(/We can't see that calendar yet/)).toBeInTheDocument();
  });

  it("sends the owner to Google to pick their calendar, saving edits first", async () => {
    const go = vi.spyOn(navigation, "go").mockImplementation(() => undefined);
    const calls = mockApi(
      routes({
        "GET /api/v1/subscribers/options": () => ({ status: 200, body: { ...OPTIONS, calendar_share_email: "bot@x.iam", google_sign_in: true } }),
        "PATCH /api/v1/subscribers/profile": () => ({ status: 200, body: PROFILE }),
        "POST /api/v1/subscribers/profile/calendar/google/start": () => ({ status: 200, body: { url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" } }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Your calendar" }));
    await user.selectOptions(screen.getByLabelText("Calendar you book appointments in"), "google");
    // Sharing by hand is still there.
    expect(screen.getByText("Or share it yourself")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Connect with Google" }));
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"));
    const keys = calls.map((c) => c.key);
    expect(keys.indexOf("PATCH /api/v1/subscribers/profile")).toBeLessThan(keys.indexOf("POST /api/v1/subscribers/profile/calendar/google/start"));
    go.mockRestore();
  });

  it("back from Google, lists the owner's calendars and connects the chosen one", async () => {
    sessionStorage.setItem("receptionist-subscriber-token", "s-1");
    window.history.replaceState(null, "", "/get-started?state=st-1&code=co-1&scope=x&iss=https%3A%2F%2Faccounts.google.com");
    let exchanged: unknown = null;
    let connected: unknown = null;
    const connectedProfile = {
      ...PROFILE,
      details: { ...EMPTY_DETAILS, calendar_provider: "google", google_calendar_id: "team@group.calendar.google.com", google_calendar_connected: true, google_calendar_name: "Team" },
    };
    mockApi(
      routes({
        "GET /api/v1/subscribers/options": () => ({ status: 200, body: { ...OPTIONS, calendar_share_email: "bot@x.iam", google_sign_in: true } }),
        "POST /api/v1/subscribers/profile/calendar/google/calendars": (init) => {
          exchanged = JSON.parse(String(init?.body));
          return {
            status: 200,
            body: {
              ticket: "ticket-1",
              calendars: [
                { id: "sam@gmail.com", name: "Sam Rivera", primary: true },
                { id: "team@group.calendar.google.com", name: "Team", primary: false },
              ],
            },
          };
        },
        "POST /api/v1/subscribers/profile/calendar/google/connect": (init) => {
          connected = JSON.parse(String(init?.body));
          return { status: 200, body: connectedProfile };
        },
      }),
    );
    render(<SubscriberApp />);
    const user = userEvent.setup({ delay: null });
    // Google's answer is taken out of the address.
    expect(window.location.search).toBe("");
    expect(await screen.findByRole("radio", { name: /Sam Rivera \(your main calendar\)/ })).toBeChecked();
    expect(exchanged).toEqual({ code: "co-1", state: "st-1" });
    await user.click(screen.getByRole("radio", { name: /Team/ }));
    await user.click(screen.getByRole("button", { name: "Connect this calendar" }));
    expect(await screen.findByText("Your calendar is connected and shared with us.")).toBeInTheDocument();
    expect(connected).toEqual({ ticket: "ticket-1", calendar_id: "team@group.calendar.google.com" });
    expect(screen.getByText("Team", { selector: ".calendar-connect strong" })).toBeInTheDocument();
    // Its name, not the long id.
    expect(screen.queryByText(/team@group\.calendar\.google\.com/)).not.toBeInTheDocument();
  });

  it("says so when the owner cancels Google's sign-in", async () => {
    sessionStorage.setItem("receptionist-subscriber-token", "s-1");
    window.history.replaceState(null, "", "/get-started?state=st-1&error=access_denied");
    mockApi(routes({ "GET /api/v1/subscribers/options": () => ({ status: 200, body: { ...OPTIONS, google_sign_in: true } }) }));
    render(<SubscriberApp />);
    expect(await screen.findByText(/Google sign-in was cancelled/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your calendar" })).toBeInTheDocument();
  });

  it("checks a connected calendar and says what's wrong with its sharing", async () => {
    const connectedProfile = {
      ...PROFILE,
      details: { ...EMPTY_DETAILS, calendar_provider: "google", google_calendar_id: "sam@gmail.com", google_calendar_connected: true, google_calendar_name: "Sam Rivera" },
    };
    let shared = true;
    mockApi(
      routes({
        "GET /api/v1/subscribers/options": () => ({ status: 200, body: { ...OPTIONS, calendar_share_email: "bot@x.iam" } }),
        "GET /api/v1/subscribers/profile": () => ({ status: 200, body: connectedProfile }),
        "POST /api/v1/subscribers/profile/calendar/check": () => ({
          status: 200,
          body: shared
            ? { ok: true, code: null, message: null }
            : { ok: false, code: "calendar_not_shared", message: "We can't see that calendar yet. Share it with bot@x.iam." },
        }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Your calendar" }));
    await user.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText(/Your calendar is working/)).toBeInTheDocument();
    shared = false;
    await user.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText(/We can't see that calendar yet/)).toBeInTheDocument();
  });

  it("offers a female and a male voice and sends the chosen one", async () => {
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
    await user.click(await screen.findByRole("button", { name: "Voice" }));
    const voices = screen.getByRole("group", { name: "How should your receptionist sound?" });
    expect(within(voices).getByLabelText("Female voice")).toBeInTheDocument();
    await user.click(within(voices).getByLabelText("Male voice"));
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
    await user.click(screen.getByRole("button", { name: "Choose a voice" }));
    expect(screen.getByRole("heading", { name: "Voice" })).toBeInTheDocument();
  });

  it("submits only after the AI disclosure is accepted, saving that agreement first", async () => {
    const order: string[] = [];
    const complete = { ...PROFILE, missing: ["ai_disclosure_ack"] };
    mockApi(
      routes({
        "GET /api/v1/subscribers/profile": () => ({ status: 200, body: complete }),
        "PATCH /api/v1/subscribers/profile": (init) => {
          order.push(`save ${init?.body}`);
          return { status: 200, body: { ...complete, missing: [] } };
        },
        "POST /api/v1/subscribers/profile/submit": () => {
          order.push("submit");
          return { status: 200, body: { ...complete, missing: [], status: "submitted" } };
        },
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.click(await screen.findByRole("button", { name: "Review and submit" }));
    const submit = screen.getByRole("button", { name: "Submit profile" });
    expect(submit).toBeDisabled();
    await user.click(screen.getByLabelText(/speaking with an AI assistant/));
    await user.click(submit);
    expect(await screen.findByText(/Submitted\./)).toBeInTheDocument();
    expect(order).toEqual(['save {"details":{"ai_disclosure_ack":true}}', "submit"]);
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
  it("reads the website after the first step and offers its hours on the next", async () => {
    let profile: Record<string, unknown> = PROFILE;
    const calls = mockApi(
      routes({
        "GET /api/v1/subscribers/profile": () => ({ status: 200, body: profile }),
        "PATCH /api/v1/subscribers/profile": (init) => {
          const update = JSON.parse(String(init?.body));
          profile = { ...profile, details: { ...EMPTY_DETAILS, ...update.details } };
          return { status: 200, body: profile };
        },
        "POST /api/v1/subscribers/profile/website/read": () => ({
          status: 200,
          body: {
            source: "https://shaggy.example/",
            pages: ["https://shaggy.example/"],
            business_phone: null,
            address: { value: "12 Market St, San Jose", quote: "Visit 12 Market St, San Jose" },
            hours: [{ weekday: 1, opens_at: "09:00", closes_at: "17:00", quote: "Tuesdays 9am to 5pm" }],
            services: [],
            team: [],
            dropped: 0,
          },
        }),
      }),
    );
    render(<SubscriberApp />);
    const user = await signUp();
    await user.type(await screen.findByLabelText("Website (optional)"), "https://shaggy.example/");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    const found = await screen.findByRole("region", { name: "Where and when on your website" });
    expect(found).toHaveTextContent("Tuesday: 9 AM to 5 PM");
    expect(found).toHaveTextContent("Address: 12 Market St, San Jose");
    expect(found).toHaveTextContent("From your website: “Visit 12 Market St, San Jose” “Tuesdays 9am to 5pm”");
    expect(calls.filter((c) => c.key === "POST /api/v1/subscribers/profile/website/read")).toHaveLength(1);

    await user.click(within(found).getByRole("button", { name: "Use these" }));
    expect(screen.queryByRole("region", { name: "Where and when on your website" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Address (optional)")).toHaveValue("12 Market St, San Jose");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));
    const saved = JSON.parse(String(calls.filter((c) => c.key === "PATCH /api/v1/subscribers/profile").at(-1)?.init?.body));
    expect(saved.details.hours).toEqual([{ weekday: 1, opens_at: "09:00", closes_at: "17:00" }]);
  });
});
