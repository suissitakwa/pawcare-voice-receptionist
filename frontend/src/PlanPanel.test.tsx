import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanPanel } from "./PlanPanel";
import type { PlanOption, PlanStatus } from "./staffApi";

const OPTIONS: PlanOption[] = [
  { plan: "starter", label: "Starter", price: "$49", calls: 100, card_payment: false },
  { plan: "growth", label: "Growth", price: "$99", calls: 300, card_payment: false },
  { plan: "pro", label: "Pro", price: "$149", calls: 500, card_payment: false },
];

const TRIAL: PlanStatus = {
  plan: "trial",
  label: "Free trial",
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
  plan_options: OPTIONS,
};

/** Stripe set up, every plan for sale. */
function byCard(status: PlanStatus): PlanStatus {
  return { ...status, card_payment: true, plan_options: OPTIONS.map((o) => ({ ...o, card_payment: true })) };
}

const STARTER = { ...TRIAL, plan: "starter" as const, label: "Starter", trial_ends_at: null };

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
    expect(screen.getByText(/Then choose a plan, from \$49 a month/)).toBeInTheDocument();
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
    mockPlan({ ...STARTER, state: "allowance_used", calls_this_month: 100 });
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

  it("offers the three plans and checks out the one chosen", async () => {
    const calls = mockPlan(byCard(TRIAL));
    const navigate = vi.fn();
    render(<PlanPanel token="t" navigate={navigate} />);
    const plans = await screen.findByRole("list", { name: "Plans" });
    expect(plans).toHaveTextContent("Starter: $49 a month for 100 calls");
    expect(plans).toHaveTextContent("Pro: $149 a month for 500 calls");
    await userEvent.setup().click(screen.getByRole("button", { name: "Growth: $99 a month for 300 calls" }));
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("https://stripe.example/api/v1/staff/plan/checkout"));
    expect(calls).toContain("POST /api/v1/staff/plan/checkout");
    const [, init] = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/checkout"))!;
    expect(JSON.parse(String(init?.body))).toEqual({ plan: "growth" });
    expect(screen.queryByRole("button", { name: /Keep my receptionist/ })).not.toBeInTheDocument();
  });

  it("only offers the plans that can be bought by card", async () => {
    const status = byCard(TRIAL);
    mockPlan({ ...status, plan_options: status.plan_options.map((o) => ({ ...o, card_payment: o.plan !== "pro" })) });
    render(<PlanPanel token="t" />);
    const plans = await screen.findByRole("list", { name: "Plans" });
    expect(plans).toHaveTextContent("Growth");
    expect(plans).not.toHaveTextContent("Pro");
  });

  it("thanks the owner back from Stripe and offers the billing portal", async () => {
    window.history.replaceState(null, "", "/staff?billing=paid");
    const growth = { ...STARTER, plan: "growth" as const, label: "Growth", price: "$99", calls_included: 300 };
    const calls = mockPlan({ ...byCard(growth), state: "active", billing_status: "active", manage_billing: true });
    const navigate = vi.fn();
    render(<PlanPanel token="t" navigate={navigate} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Thanks for paying. Your receptionist is on Growth.");
    expect(window.location.search).toBe("");
    expect(screen.getByText(/Growth: \$99 a month/)).toBeInTheDocument();
    expect(screen.getByText(/To change plan, choose Manage billing/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Plans" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Manage billing" }));
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("https://stripe.example/api/v1/staff/plan/portal"));
    expect(calls).toContain("POST /api/v1/staff/plan/portal");
  });

  it("warns about a failed card and a stopped subscription", async () => {
    const paid = { ...byCard(STARTER), manage_billing: true };
    mockPlan({ ...paid, state: "active", billing_status: "past_due" });
    const { unmount } = render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your last card payment didn't go through");
    unmount();
    mockPlan({ ...paid, state: "payment_stopped", billing_status: "canceled" });
    render(<PlanPanel token="t" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your card payments have stopped");
    expect(screen.getByRole("list", { name: "Plans" })).toBeInTheDocument();
  });

  it("says when a cancelled subscription ends, and that it works until then", async () => {
    mockPlan({
      ...byCard(STARTER),
      state: "active",
      billing_status: "active",
      billing_ends_at: "2027-12-24T18:00:00Z",
      manage_billing: true,
    });
    render(<PlanPanel token="t" />);
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Cancels on December 24.");
    expect(notice).toHaveTextContent("works in full until then");
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Plans" })).not.toBeInTheDocument();
  });
});
