import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WebsiteFindings, type WebsiteDetails, type WebsiteState } from "./WebsiteFindings";
import type { ProfileDetails } from "./subscriberApi";

const DETAILS: ProfileDetails = {
  business_phone: null,
  website_url: "https://shaggy.example/",
  calendar_provider: null,
  calendar_other: null,
  knowledge_urls: [],
  faqs: [],
  voice: null,
  ai_disclosure_ack: false,
  services: [],
  team: [],
};

const FOUND: WebsiteDetails = {
  source: "https://shaggy.example/",
  pages: ["https://shaggy.example/", "https://shaggy.example/services"],
  business_phone: { value: "(408) 555-0134", quote: "Call us at (408) 555-0134" },
  address: null,
  hours: [],
  services: [
    {
      name: "Full Groom",
      description: "Bath, haircut and nails.",
      prices: [
        { size: "small", duration_minutes: 90, price_cents: 6500 },
        { size: "large", duration_minutes: null, price_cents: 10500 },
      ],
      quote: "Full Groom: small dogs $65, 90 minutes. Large dogs $105",
    },
    { name: "Nail Trim", description: "", prices: [], quote: "Nail Trim $15" },
  ],
  team: [
    { name: "Sam", quote: "Your groomers: Sam and Jo" },
    { name: "Jo", quote: "Your groomers: Sam and Jo" },
  ],
  dropped: 1,
};

const done: WebsiteState = { url: "https://shaggy.example/", status: "done", found: FOUND, used: [] };

function show(step: string, state: WebsiteState | null, details: ProfileDetails = DETAILS) {
  const onChange = vi.fn();
  const onUsed = vi.fn();
  const onRetry = vi.fn();
  render(
    <WebsiteFindings step={step} state={state} details={details} isMobile={false} onChange={onChange} onUsed={onUsed} onRetry={onRetry} />,
  );
  return { onChange, onUsed, onRetry, user: userEvent.setup() };
}

describe("WebsiteFindings", () => {
  it("adds the website's services, keeping blanks for the owner to fill in", async () => {
    const { onChange, onUsed, user } = show("services", done, {
      ...DETAILS,
      services: [{ name: "nail trim", description: "", prices: [] }],
    });
    const found = screen.getByRole("region", { name: "Services on your website" });
    // A service the owner already has isn't offered again.
    expect(found).toHaveTextContent("Full Groom: $65, 90 minutes; $105");
    expect(found).not.toHaveTextContent("Nail Trim");
    expect(found).toHaveTextContent("a row without both isn't saved");
    await user.click(within(found).getByRole("button", { name: "Add this service" }));
    expect(onChange).toHaveBeenCalledWith({
      services: [{ name: "nail trim", description: "", prices: [] }, { name: "Full Groom", description: "Bath, haircut and nails.", prices: FOUND.services[0].prices }],
    });
    expect(onUsed).toHaveBeenCalledWith("services");
  });

  it("offers the phone number only while the owner has none", async () => {
    const { onChange, user } = show("business", done);
    await user.click(screen.getByRole("button", { name: "Use this number" }));
    expect(onChange).toHaveBeenCalledWith({ business_phone: "(408) 555-0134" });
  });

  it("adds the team, and can be turned down", async () => {
    const { onChange, onUsed, user } = show("team", done);
    expect(screen.getByRole("region", { name: "People on your website" })).toHaveTextContent("Sam, Jo");
    await user.click(screen.getByRole("button", { name: "Not these" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(onUsed).toHaveBeenCalledWith("team");
  });

  it("shows nothing once a section was used or turned down", () => {
    show("team", { ...done, used: ["team"] });
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("says while it reads, and why it couldn't", async () => {
    const { unmount } = render(
      <WebsiteFindings step="hours" state={{ url: "x", status: "reading" }} details={DETAILS} isMobile={false} onChange={vi.fn()} onUsed={vi.fn()} onRetry={vi.fn()} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Reading your website");
    unmount();
    const { onRetry, user } = show("hours", { url: "x", status: "failed", message: "That page couldn't be reached." });
    expect(screen.getByRole("status")).toHaveTextContent("We couldn't read your website: That page couldn't be reached.");
    await user.click(screen.getByRole("button", { name: "Try reading it again" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
