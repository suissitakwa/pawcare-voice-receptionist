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
};

function mockPlan(get: PlanStatus, post?: PlanStatus) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${new URL(url).pathname}`);
      const body = init?.method === "POST" ? post : get;
      return { ok: true, status: 200, json: () => Promise.resolve(body) };
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

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
});
