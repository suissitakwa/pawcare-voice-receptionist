import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeSuggestions } from "./KnowledgeSuggestions";
import type { Profile } from "./subscriberApi";

const PROFILE: Profile = {
  status: "draft",
  business_name: "Shaggy Chic",
  business_type: "pet_grooming",
  details: {
    business_phone: null,
    website_url: null,
    calendar_provider: null,
    calendar_other: null,
    knowledge_urls: ["https://shaggy.example/faq"],
    faqs: [],
    voice: null,
    ai_disclosure_ack: false,
  },
  documents: [{ id: "d1", filename: "prices.pdf", content_type: "application/pdf", size_bytes: 10, created_at: "x" }],
  missing: [],
  submitted_at: null,
  updated_at: "x",
};

const LATE = {
  question: "What if I cancel late?",
  answer: "Late cancellations are charged $20.",
  quote: "Late cancellations are charged $20",
};
const CATS = { question: "Do you groom cats?", answer: "Dogs only.", quote: "We groom dogs only" };

type Reply = { status: number; body: unknown };

function mockSuggest(replies: Record<string, Reply>) {
  const sent: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      sent.push(body);
      const reply = replies[body.url ?? body.document_id];
      return { ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body) };
    }),
  );
  return sent;
}

function problem(code: string, message: string, status = 422): Reply {
  return { status, body: { error: { code, message } } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KnowledgeSuggestions", () => {
  it("reads each saved source and lets the owner add or skip each answer", async () => {
    const sent = mockSuggest({
      "https://shaggy.example/faq": { status: 200, body: { source: "https://shaggy.example/faq", kind: "page", answers: [LATE, CATS], dropped: 1 } },
      d1: problem("document_unreadable", "That PDF's text couldn't be read."),
    });
    const prepare = vi.fn(async () => PROFILE);
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(<KnowledgeSuggestions token="s-1" faqs={[]} prepare={prepare} onAdd={onAdd} onSignedOut={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Suggest answers from my pages and documents" }));

    expect(await screen.findByText(/Found 2 possible answers/)).toBeInTheDocument();
    expect(prepare).toHaveBeenCalledOnce();
    expect(sent).toEqual([{ url: "https://shaggy.example/faq" }, { document_id: "d1" }]);
    expect(screen.getByRole("alert")).toHaveTextContent("prices.pdf: That PDF's text couldn't be read.");
    expect(screen.getByText(/From https:\/\/shaggy.example\/faq: “Late cancellations are charged \$20”/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add “What if I cancel late?”" }));
    expect(onAdd).toHaveBeenCalledWith({ question: LATE.question, answer: LATE.answer });
    await user.click(screen.getByRole("button", { name: "Skip “Do you groom cats?”" }));
    expect(screen.queryByText("Do you groom cats?")).not.toBeInTheDocument();
  });

  it("hides answers to questions the owner already has", async () => {
    mockSuggest({
      "https://shaggy.example/faq": { status: 200, body: { source: "x", kind: "page", answers: [LATE, CATS], dropped: 0 } },
      d1: { status: 200, body: { source: "prices.pdf", kind: "document", answers: [], dropped: 0 } },
    });
    const user = userEvent.setup();
    render(
      <KnowledgeSuggestions
        token="s-1"
        faqs={[{ question: "do you groom cats?", answer: "No." }]}
        prepare={async () => PROFILE}
        onAdd={vi.fn()}
        onSignedOut={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Suggest answers from my pages and documents" }));
    expect(await screen.findByText("What if I cancel late?")).toBeInTheDocument();
    expect(screen.queryByText("Do you groom cats?")).not.toBeInTheDocument();
  });

  it("stops when suggestions aren't available", async () => {
    const sent = mockSuggest({
      "https://shaggy.example/faq": problem("suggestions_unavailable", "Suggested answers aren't available yet.", 503),
      d1: { status: 200, body: { source: "prices.pdf", kind: "document", answers: [], dropped: 0 } },
    });
    const user = userEvent.setup();
    render(<KnowledgeSuggestions token="s-1" faqs={[]} prepare={async () => PROFILE} onAdd={vi.fn()} onSignedOut={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Suggest answers from my pages and documents" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Suggested answers aren't available yet.");
    expect(sent).toHaveLength(1);
    expect(screen.queryByText(/possible answer|No answers/)).not.toBeInTheDocument();
  });

  it("asks for a source when there is none", async () => {
    const user = userEvent.setup();
    const empty = { ...PROFILE, documents: [], details: { ...PROFILE.details, knowledge_urls: [] } };
    render(<KnowledgeSuggestions token="s-1" faqs={[]} prepare={async () => empty} onAdd={vi.fn()} onSignedOut={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Suggest answers from my pages and documents" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Add a web page or upload a document first.");
  });
});
