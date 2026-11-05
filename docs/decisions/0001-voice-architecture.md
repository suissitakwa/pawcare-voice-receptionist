# 0001: Voice architecture

**Status:** Connection flow implemented in migration 0006, `app/voice.py`, and `VoiceAssistant.tsx`. Tool transport is still provisional.

## Context

The browser must hold a live audio conversation with low latency, the permanent provider key must never reach it, and business actions must stay under backend control.

Verified against OpenAI's documentation on 2026-11-04:

- `POST /v1/realtime/client_secrets` mints a short-lived credential from the server.
- The browser posts its SDP offer to `POST /v1/realtime/calls` using that credential. Audio travels on WebRTC media tracks, and events on a data channel named `oai-events`.
- Function calls arrive as `function_call` items in `response.done`. The client returns a `function_call_output` item, then sends `response.create`.
- On WebRTC, the server truncates unplayed audio automatically when the user interrupts.
- A server can attach to the same call as a "sideband" WebSocket using the call ID.
- OpenAI's current WebRTC examples use the model `gpt-realtime-2.1`. OpenAI also offers a separate GPT-Live API, which is not what this project specified.

## Decision

1. **Browser connects directly to OpenAI over WebRTC** with a backend-minted credential. The backend does not relay audio, which keeps it small and adds no audio latency.
2. **The backend sets instructions and tools when minting the credential.** The model name is configuration, re-verified before the first real call, and never hardcoded in the client.
3. **Start with the browser relaying tool calls** to authenticated backend endpoints, scoped by a server-issued voice session.
4. **Booking confirmation requires a real click** on a visible control. A model tool call alone cannot commit a booking.

## Tradeoff: browser relay or server sideband

| | Browser relay (chosen first) | Server sideband |
|---|---|---|
| Moving parts | Plain HTTP endpoints | One long-lived WebSocket per call on the backend |
| Forged tool calls | Possible, so the backend must treat each one as hostile | Tool calls never pass through the browser |
| Works with test mode | Same endpoints, no provider needed | Needs a separate simulation path |
| Failure handling | Page and connection fail together | Backend must also handle sideband loss |

The relay is safe here because a forged call gains nothing: each tool can do no more than the public booking form, under the same validation and rate limits, and commit still requires the bound confirmation. **Instructions in the browser or model are not treated as a security control.** Revisit the sideband if a tool ever needs authority the public form does not have.

## Consequences

- Every tool endpoint needs validation, rate limits, and session scoping.
- A visitor can technically see their own session's tool traffic in developer tools. It must not contain anything they could not already see.
- Minting credentials costs money, so it needs its own rate limit, daily quota, and disable switch before voice goes live.

## Implementation notes

- The credential lives for 60 seconds, enough to start one call. It cannot extend a call that has started.
- The default model is `gpt-realtime-2.1-mini`, chosen for cost. It is configurable through `VOICE_MODEL`.
- Instructions are built on the server from database records: hours, sizes, services, demo prices, and approved FAQs. Until tools exist, the assistant is told it cannot check availability or book, and to send callers to the booking form.
- Caller speech is transcribed with `gpt-4o-mini-transcribe` so the visitor sees a transcript. Transcripts are not sent to the backend or stored.
- Voice activity detection lets the caller interrupt the assistant mid-sentence.
- Only session outcomes and token counts are stored, never audio or transcripts. Visitor addresses are stored only as hashes.
