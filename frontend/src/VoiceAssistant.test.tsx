import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VoiceAssistant, { type RealtimeDeps } from "./VoiceAssistant";

const LIVE = {
  session_id: "s-1",
  session_token: "t".repeat(43),
  mode: "live",
  client_secret: "ek_test",
  expires_at: "2027-01-05T18:01:00Z",
  max_duration_seconds: 300,
};

class FakeChannel extends EventTarget {
  sent: unknown[] = [];
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  emit(event: unknown) {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(event) }));
  }
  open() {
    this.dispatchEvent(new Event("open"));
  }
}

class FakePeer {
  channel = new FakeChannel();
  closed = false;
  connectionState = "new";
  ontrack: ((event: unknown) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  addTrack = vi.fn();
  getSenders = () => [];
  createDataChannel = vi.fn(() => this.channel);
  createOffer = vi.fn(async () => ({ type: "offer", sdp: "v=0 offer" }));
  setLocalDescription = vi.fn(async () => undefined);
  setRemoteDescription = vi.fn(async () => undefined);
  close = vi.fn(() => {
    this.closed = true;
  });
}

function fakeMicrophone() {
  const track = { enabled: true, stop: vi.fn() };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  return { track, stream };
}

type Handler = (init?: RequestInit) => { status: number; body: unknown } | "network";

function mockBackend(session: unknown = LIVE, status = 201, routes: Record<string, Handler> = {}) {
  const ends: { reason: string; usage: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/end")) {
        ends.push(JSON.parse(String(init?.body)));
        return { ok: true, status: 204, json: () => Promise.resolve(null) };
      }
      const route = Object.keys(routes).find((suffix) => url.endsWith(suffix));
      if (route) {
        const reply = routes[route](init);
        if (reply === "network") throw new TypeError("Failed to fetch");
        return { ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body) };
      }
      return { ok: status < 400, status, json: () => Promise.resolve(session) };
    }),
  );
  return ends;
}

const SUMMARY = {
  service: "Full Groom",
  size: "Medium",
  pet_name: "Biscuit",
  owner_name: "Jordan",
  owner_email: null,
  owner_phone: "(323) 555-0100",
  starts_at: "2027-01-05T17:00:00Z",
  local_start: "2027-01-05T09:00:00-08:00",
  local_end: "2027-01-05T11:00:00-08:00",
  timezone: "America/Los_Angeles",
  duration_minutes: 120,
  price_cents: 9000,
};
const PREPARED = { confirmation_token: "secret-token-1234567890", summary_hash: "h".repeat(64), expires_at: "", summary: SUMMARY };
const PREPARE_RESULT = { ok: true, result: { status: "awaiting_caller_confirmation", time: "9 AM" }, prepared: PREPARED };

async function connectedCall(routes: Record<string, Handler>) {
  mockBackend(LIVE, 201, routes);
  const ctx = setup();
  const user = userEvent.setup({ delay: null });
  render(<VoiceAssistant deps={ctx.deps} />);
  await user.click(screen.getByRole("button", { name: "Start talking" }));
  await waitFor(() => expect(ctx.peer.setRemoteDescription).toHaveBeenCalled());
  act(() => {
    Object.defineProperty(ctx.peer.channel, "readyState", { value: "open" });
    ctx.peer.channel.open();
  });
  return { ...ctx, user };
}

function requestPrepare(peer: FakePeer) {
  act(() =>
    peer.channel.emit({
      type: "response.done",
      response: {
        status: "completed",
        output: [{ type: "function_call", name: "prepare_booking", call_id: "call_1", arguments: '{"service":"full-groom"}' }],
      },
    }),
  );
}

function setup(overrides: Partial<RealtimeDeps> = {}) {
  const peer = new FakePeer();
  const mic = fakeMicrophone();
  const deps: RealtimeDeps = {
    createPeerConnection: () => peer as unknown as RTCPeerConnection,
    getMicrophone: vi.fn(async () => mic.stream),
    postOffer: vi.fn(async () => "v=0 answer"),
    ...overrides,
  };
  return { peer, mic, deps };
}

beforeEach(() => {
  vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("VoiceAssistant", () => {
  it("starts idle, even when React mounts it twice in development", () => {
    const ends = mockBackend();
    const { deps } = setup();
    render(
      <StrictMode>
        <VoiceAssistant deps={deps} />
      </StrictMode>,
    );
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start talking" })).toBeInTheDocument();
    expect(ends).toHaveLength(0);
  });

  it("connects, shows live states and transcript, and greets first", async () => {
    mockBackend();
    const { peer, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));

    await waitFor(() => expect(deps.postOffer).toHaveBeenCalledWith("v=0 offer", "ek_test"));
    expect(peer.createDataChannel).toHaveBeenCalledWith("oai-events");
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalledWith({ type: "answer", sdp: "v=0 answer" }));

    act(() => peer.channel.open());
    expect(screen.getByText("Listening")).toBeInTheDocument();
    expect(peer.channel.sent).toEqual([{ type: "response.create" }]);

    act(() => peer.channel.emit({ type: "output_audio_buffer.started", response_id: "r1" }));
    expect(screen.getByText("Speaking")).toBeInTheDocument();
    act(() => peer.channel.emit({ type: "response.output_audio_transcript.delta", item_id: "a1", delta: "Hi, how " }));
    act(() => peer.channel.emit({ type: "response.output_audio_transcript.delta", item_id: "a1", delta: "can I help?" }));
    expect(screen.getByText("Hi, how can I help?")).toBeInTheDocument();

    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_started", item_id: "u1" }));
    expect(screen.getByText("Hearing you")).toBeInTheDocument();
    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_stopped", item_id: "u1" }));
    expect(screen.getByText("Thinking")).toBeInTheDocument();
    act(() =>
      peer.channel.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "u1", transcript: " What are your hours? " }),
    );
    expect(screen.getByText("What are your hours?")).toBeInTheDocument();
  });

  it("keeps the caller's words before the reply they triggered", async () => {
    mockBackend();
    const { peer, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    act(() => peer.channel.open());

    // The order the events really arrive in: the reply starts before transcription finishes.
    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_started", item_id: "u1" }));
    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_stopped", item_id: "u1" }));
    act(() => peer.channel.emit({ type: "response.output_audio_transcript.delta", item_id: "a1", delta: "Awesome!" }));
    act(() => peer.channel.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "u1", transcript: "Yes." }));

    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["YouYes.", "AssistantAwesome!"]);
  });

  it("drops empty transcriptions and marks failed ones", async () => {
    mockBackend();
    const { peer, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    act(() => peer.channel.open());

    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_started", item_id: "u1" }));
    act(() => peer.channel.emit({ type: "conversation.item.input_audio_transcription.completed", item_id: "u1", transcript: "  " }));
    act(() => peer.channel.emit({ type: "input_audio_buffer.speech_started", item_id: "u2" }));
    act(() => peer.channel.emit({ type: "conversation.item.input_audio_transcription.failed", item_id: "u2", error: {} }));

    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["You(not transcribed)"]);
  });

  it("runs a tool through the backend and returns only the safe result to the model", async () => {
    const bodies: unknown[] = [];
    const { peer } = await connectedCall({
      "/tools/prepare_booking": (init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return { status: 200, body: PREPARE_RESULT };
      },
    });
    requestPrepare(peer);

    expect(await screen.findByRole("region", { name: "Booking summary" })).toHaveTextContent("Full Groom, medium dog");
    expect(bodies[0]).toEqual({ session_token: LIVE.session_token, arguments: '{"service":"full-groom"}' });
    const output = peer.channel.sent.find((e) => (e as { item?: { type?: string } }).item?.type === "function_call_output") as {
      item: { call_id: string; output: string };
    };
    expect(output.item.call_id).toBe("call_1");
    expect(output.item.output).not.toContain("secret-token");
    expect(peer.channel.sent.at(-1)).toEqual({ type: "response.create" });
  });

  it("books only when the visitor clicks Confirm, then tells the assistant the reference", async () => {
    const confirmed: RequestInit[] = [];
    const { peer, user } = await connectedCall({
      "/tools/prepare_booking": () => ({ status: 200, body: PREPARE_RESULT }),
      "/bookings/confirm": (init) => {
        confirmed.push(init!);
        return { status: 201, body: { reference: "PC-VOICE234", status: "confirmed", summary: SUMMARY } };
      },
    });
    requestPrepare(peer);
    await screen.findByRole("region", { name: "Booking summary" });
    expect(confirmed).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByText("PC-VOICE234")).toBeInTheDocument();
    expect((confirmed[0].headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    const message = peer.channel.sent.find((e) => (e as { item?: { role?: string } }).item?.role === "system") as {
      item: { content: { text: string }[] };
    };
    expect(message.item.content[0].text).toContain("PC-VOICE234");
    expect(screen.queryByRole("region", { name: "Booking summary" })).not.toBeInTheDocument();
  });

  it("explains a slot taken before the click", async () => {
    const { peer, user } = await connectedCall({
      "/tools/prepare_booking": () => ({ status: 200, body: PREPARE_RESULT }),
      "/bookings/confirm": () => ({ status: 409, body: { error: { code: "slot_unavailable", message: "Taken" } } }),
    });
    requestPrepare(peer);
    await user.click(await screen.findByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("just taken");
    const message = peer.channel.sent.find((e) => (e as { item?: { role?: string } }).item?.role === "system") as {
      item: { content: { text: string }[] };
    };
    expect(message.item.content[0].text).toContain("booked by someone else");
  });

  it("tells the model when a tool cannot be reached instead of leaving it waiting", async () => {
    const { peer } = await connectedCall({ "/tools/prepare_booking": () => "network" });
    requestPrepare(peer);
    await waitFor(() =>
      expect(peer.channel.sent.some((e) => (e as { item?: { type?: string } }).item?.type === "function_call_output")).toBe(true),
    );
    const output = peer.channel.sent.find((e) => (e as { item?: { type?: string } }).item?.type === "function_call_output") as {
      item: { output: string };
    };
    expect(JSON.parse(output.item.output).error).toBe("tool_unavailable");
    expect(screen.queryByRole("region", { name: "Booking summary" })).not.toBeInTheDocument();
  });

  it("mutes the microphone track without ending the call", async () => {
    mockBackend();
    const { peer, mic, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    act(() => peer.channel.open());

    await user.click(screen.getByRole("button", { name: "Mute" }));
    expect(mic.track.enabled).toBe(false);
    expect(screen.getByRole("button", { name: "Unmute" })).toHaveAttribute("aria-pressed", "true");
    expect(peer.closed).toBe(false);
    await user.click(screen.getByRole("button", { name: "Unmute" }));
    expect(mic.track.enabled).toBe(true);
  });

  it("ending stops the microphone, closes the connection, and reports usage", async () => {
    const ends = mockBackend();
    const { peer, mic, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    act(() => peer.channel.open());
    act(() => peer.channel.emit({ type: "response.done", response: { status: "completed", usage: { input_tokens: 50, output_tokens: 80 } } }));
    act(() =>
      peer.channel.emit({ type: "response.done", response: { status: "incomplete", status_details: { reason: "max_output_tokens" } } }),
    );
    act(() =>
      peer.channel.emit({ type: "response.done", response: { status: "cancelled", status_details: { reason: "turn_detected" } } }),
    );

    await user.click(screen.getByRole("button", { name: "End call" }));
    expect(mic.track.stop).toHaveBeenCalled();
    expect(peer.closed).toBe(true);
    expect(screen.getByText("Call ended")).toBeInTheDocument();
    await waitFor(() => expect(ends).toHaveLength(1));
    expect(ends[0]).toMatchObject({
      reason: "user_ended",
      usage: { input_tokens: 50, output_tokens: 80, responses: 3, incomplete_responses: 1, interrupted_responses: 1 },
    });
  });

  it("explains a blocked microphone and offers the booking form", async () => {
    const ends = mockBackend();
    const { peer, deps } = setup({
      getMicrophone: vi.fn(async () => {
        throw new DOMException("denied", "NotAllowedError");
      }),
    });
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone access was blocked");
    expect(screen.getByRole("alert")).toHaveTextContent("booking form");
    expect(peer.createDataChannel).not.toHaveBeenCalled();
    await waitFor(() => expect(ends[0]?.reason).toBe("mic_denied"));
    expect(screen.getByRole("button", { name: "Start a new call" })).toBeInTheDocument();
  });

  it("cleans up and reports an error when the connection fails", async () => {
    const ends = mockBackend();
    const { mic, peer, deps } = setup({ postOffer: vi.fn(async () => Promise.reject(new Error("503"))) });
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't connect");
    expect(mic.track.stop).toHaveBeenCalled();
    expect(peer.closed).toBe(true);
    await waitFor(() => expect(ends[0]?.reason).toBe("error"));
  });

  it("ends the call when the connection drops", async () => {
    const ends = mockBackend();
    const { peer, mic, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    act(() => {
      peer.connectionState = "failed";
      peer.onconnectionstatechange?.();
    });
    expect(screen.getByRole("status")).toHaveTextContent("connection dropped");
    expect(mic.track.stop).toHaveBeenCalled();
    await waitFor(() => expect(ends[0]?.reason).toBe("disconnected"));
  });

  it("ends automatically at the time limit", async () => {
    const ends = mockBackend({ ...LIVE, max_duration_seconds: 1 });
    const { peer, mic, deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    expect(await screen.findByText(/time limit/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(mic.track.stop).toHaveBeenCalled();
    expect(ends[0]?.reason).toBe("timeout");
  });

  it("stops the microphone if the page goes away mid-call", async () => {
    const ends = mockBackend();
    const { peer, mic, deps } = setup();
    const user = userEvent.setup({ delay: null });
    const { unmount } = render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    await waitFor(() => expect(peer.setRemoteDescription).toHaveBeenCalled());
    unmount();
    expect(mic.track.stop).toHaveBeenCalled();
    expect(peer.closed).toBe(true);
    await waitFor(() => expect(ends[0]?.reason).toBe("page_closed"));
  });

  it("test mode is clearly labelled and never touches the microphone or provider", async () => {
    mockBackend({ ...LIVE, mode: "test", client_secret: null });
    const { deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    expect(await screen.findByRole("note")).toHaveTextContent("Test mode");
    expect(screen.getByRole("note")).toHaveTextContent("No microphone is used, no AI is involved");
    expect(await screen.findByText(/thanks for calling/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(deps.getMicrophone).not.toHaveBeenCalled();
    expect(deps.postOffer).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Mute" })).not.toBeInTheDocument();
  });

  it("shows the server's message when voice is switched off", async () => {
    mockBackend({ error: { code: "voice_disabled", message: "The voice assistant is turned off right now." } }, 503);
    const { deps } = setup();
    const user = userEvent.setup({ delay: null });
    render(<VoiceAssistant deps={deps} />);
    await user.click(screen.getByRole("button", { name: "Start talking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("turned off right now");
    expect(deps.getMicrophone).not.toHaveBeenCalled();
  });
});
