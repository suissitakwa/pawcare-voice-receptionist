import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanPanel } from "./PlanPanel";
import type { PlanStatus } from "./staffApi";

const TRIAL: PlanStatus = {
  plan: "trial",
  state: "trial",
  price: "$49",
  calls_included: 100,
  calls_this_month: 12,
  trial_ends_at: new Date(Date.now() + 9.5 * 86_400_000).toISOString(),
  upgrade_requested_at: null,
  billing_status: null,
  billing_ends_at: null,
  card_payment: false,
  manage_billing: false,
};

function mockPlan(get: PlanStatus, post?: PlanStatus) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      calls.push(`${init?.method ?? "GET"} ${path}`);
      const redirect = { url: `https://stripe.example${path}` };
      const body = init?.method !== "POST" ? get : path.endsWith("/request") ? post : redirect;
      return { ok: true, status: 200, json: () => Promise.resolve(body) };
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("PlanPanel", () => {
  it("shows the trial, its calls, and asks to keep the receptionist", async () => {
    const calls = mockPlan(TRIAL, { ...TRIAL, upgrade_requested_at: new Date().toISOString() });
    render(<PlanPanel token="t" />);
    expect(await screen.findByText(/Free trial: 10 days left/)).toBeInTheDocument();
    expect(screen.getByText(/Then \$49 a month for up to 100 calls/)).toBeInTheDocument();
    expect(screen.getByText("12 of 100 calls this month.")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Keep my receptionist ($49 a month)" }));
    expect(await screen.findByRole("status")).toHaveTextContent("We'll email you an invoice.");
    expect(calls).toContain("POST /api/v1/staff/plan/request");
    expect(screen.queryByRole("button", { name: /Keep my receptionist/ })).not.toBeInTheDocument();
  });

  it("says when the trial has ended and the receptionist only takes messages", async () => {
    mockPlan({ ...TRIAL, state: "trial_ended", trial_ends_at: "2027-01-01T18:00:00Z" });
    render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your free trial ended on January 1");
    expect(screen.getByRole("alert")).toHaveTextContent("only takes messages");
    expect(screen.getByRole("button", { name: /Keep my receptionist/ })).toBeInTheDocument();
  });

  it("warns when the month's calls are used up on the paid plan", async () => {
    mockPlan({ ...TRIAL, plan: "starter", state: "allowance_used", trial_ends_at: null, calls_this_month: 100 });
    render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You've used this month's 100 calls");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows nothing for businesses without a plan", async () => {
    const calls = mockPlan({ ...TRIAL, plan: "unlimited", state: "unlimited", calls_included: null, trial_ends_at: null });
    const { container } = render(<PlanPanel token="t" />);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("sends the owner to Stripe to pay by card when it's set up", async () => {
    const calls = mockPlan({ ...TRIAL, card_payment: true });
    const navigate = vi.fn();
    render(<PlanPanel token="t" navigate={navigate} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Pay by card ($49 a month)" }));
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("https://stripe.example/api/v1/staff/plan/checkout"));
    expect(calls).toContain("POST /api/v1/staff/plan/checkout");
    expect(screen.queryByRole("button", { name: /Keep my receptionist/ })).not.toBeInTheDocument();
  });

  it("thanks the owner back from Stripe and offers the billing portal", async () => {
    window.history.replaceState(null, "", "/staff?billing=paid");
    const paid = { ...TRIAL, plan: "starter" as const, state: "active" as const, trial_ends_at: null };
    const calls = mockPlan({ ...paid, billing_status: "active", card_payment: true, manage_billing: true });
    const navigate = vi.fn();
    render(<PlanPanel token="t" navigate={navigate} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Thanks for paying");
    expect(window.location.search).toBe("");
    expect(screen.queryByRole("button", { name: /Pay by card/ })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Manage billing" }));
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("https://stripe.example/api/v1/staff/plan/portal"));
    expect(calls).toContain("POST /api/v1/staff/plan/portal");
  });

  it("warns about a failed card and a stopped subscription", async () => {
    const paid = { ...TRIAL, plan: "starter" as const, trial_ends_at: null, card_payment: true, manage_billing: true };
    mockPlan({ ...paid, state: "active", billing_status: "past_due" });
    const { unmount } = render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your last card payment didn't go through");
    unmount();
    mockPlan({ ...paid, state: "payment_stopped", billing_status: "canceled" });
    render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your card payments have stopped");
    expect(screen.getByRole("button", { name: "Pay by card ($49 a month)" })).toBeInTheDocument();
  });

  it("says when a cancelled subscription ends, and that it works until then", async () => {
    mockPlan({
      ...TRIAL,
      plan: "starter",
      state: "active",
      trial_ends_at: null,
      billing_status: "active",
      billing_ends_at: "2027-12-24T18:00:00Z",
      card_payment: true,
      manage_billing: true,
    });
    render(<PlanPanel token="t" />);
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Cancels on December 24.");
    expect(notice).toHaveTextContent("works in full until then");
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay by card/ })).not.toBeInTheDocument();
  });
});
