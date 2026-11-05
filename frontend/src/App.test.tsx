import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const BUSINESS = {
  name: "Record From Database",
  tagline: "Tagline from the API",
  about: "About text from the API",
  timezone: "America/Los_Angeles",
  contact_email: "hello@pawcare.example",
  contact_phone: "(323) 555-0142",
  address: "100 Sample Street",
  is_demo: true,
  hours: [{ weekday: 1, opens_at: "09:00:00", closes_at: "17:30:00" }],
};

const CATALOG = {
  size_categories: [
    { slug: "small", label: "Tiny Label", description: "Under 25 lb", requires_staff_assessment: false },
    { slug: "giant", label: "Giant Label", description: "Staff assess first", requires_staff_assessment: true },
  ],
  services: [
    {
      slug: "wash",
      name: "Service From Database",
      description: "Description from the API",
      options: [{ size: "small", duration_minutes: 90, price_cents: 4550 }],
    },
  ],
};

const FAQS = [{ slug: "one", question: "Question from the API?", answer: "Answer from the API." }];

function respond(url: string, overrides: Record<string, unknown> = {}) {
  const bodies: Record<string, unknown> = {
    "/api/v1/business": BUSINESS,
    "/api/v1/services": CATALOG,
    "/api/v1/faqs": FAQS,
    ...overrides,
  };
  const path = Object.keys(bodies).find((candidate) => url.endsWith(candidate));
  return Promise.resolve({ ok: true, json: () => Promise.resolve(path && bodies[path]) });
}

function mockFetchOk(overrides: Record<string, unknown> = {}) {
  return vi.fn((url: string) => respond(url, overrides));
}

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("renders branding from the API, not from hardcoded values", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    expect(await screen.findByText("Record From Database")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Tagline from the API");
    expect(screen.getByText("About text from the API")).toBeInTheDocument();
  });

  it("always shows the demo notice, even before data loads", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<App />);
    expect(screen.getByRole("note")).toHaveTextContent("Demonstration site");
    expect(screen.getByRole("note")).toHaveTextContent("do not enter");
  });

  it("exposes both primary actions as links to their sections", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    await screen.findByText("Record From Database");
    expect(screen.getByRole("link", { name: "Talk to our assistant" })).toHaveAttribute("href", "#assistant");
    expect(screen.getByRole("link", { name: "Book an appointment" })).toHaveAttribute("href", "#booking");
  });

  it("shows an error and recovers on retry when the API fails", async () => {
    const fetchMock = vi.fn((url: string) => respond(url) as Promise<unknown>);
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not be loaded");

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(await screen.findByText("Record From Database")).toBeInTheDocument();
  });

  it("treats a non-2xx response as an error rather than rendering it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<App />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("renders services, durations, and prices from the API", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Service From Database" })).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Tiny Label/ });
    expect(row).toHaveTextContent("1 hr 30 min");
    expect(row).toHaveTextContent("$45.50");
  });

  it("flags sizes that need staff assessment instead of offering a price", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    expect(await screen.findByText("Staff assess first")).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Giant Label/ })).not.toBeInTheDocument();
  });

  it("renders hours in business time and marks other days closed", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    expect(await screen.findByRole("row", { name: /Tuesday/ })).toHaveTextContent("9 AM to 5:30 PM");
    expect(screen.getByRole("row", { name: /Sunday/ })).toHaveTextContent("Closed");
  });

  it("renders approved FAQs from the API", async () => {
    vi.stubGlobal("fetch", mockFetchOk());
    render(<App />);
    expect(await screen.findByText("Question from the API?")).toBeInTheDocument();
    expect(screen.getByText("Answer from the API.")).toBeInTheDocument();
  });

  it("shows an error when a list endpoint returns an HTML page with status 200", async () => {
    vi.stubGlobal("fetch", mockFetchOk({ "/api/v1/faqs": "<!doctype html><html></html>" }));
    render(<App />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Service From Database")).not.toBeInTheDocument();
  });
});
