import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiRequestError,
  endVoiceSession,
  startVoiceSession,
  type VoiceEndReason,
  type VoiceSession,
  type VoiceUsage,
} from "./api";

export type VoiceStatus =
  | "idle"
  | "starting"
  | "listening"
  | "hearing"
  | "thinking"
  | "speaking"
  | "ended"
  | "error";

export interface TranscriptLine {
  id: string;
  speaker: "caller" | "assistant";
  text: string;
  final: boolean;
}

const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

const STATUS_TEXT: Record<VoiceStatus, string> = {
  idle: "Not connected",
  starting: "Connecting…",
  listening: "Listening",
  hearing: "Hearing you",
  thinking: "Thinking",
  speaking: "Speaking",
  ended: "Call ended",
  error: "Something went wrong",
};

// A short, fixed walkthrough for test mode. It never contacts a provider.
const TEST_SCRIPT: [TranscriptLine["speaker"], string, VoiceStatus][] = [
  ["assistant", "Hi, thanks for calling PawCare Grooming Studio. How can I help?", "speaking"],
  ["caller", "How much is a full groom for a medium dog?", "hearing"],
  ["assistant", "A full groom for a medium dog takes two hours and the demo price is 90 dollars.", "speaking"],
  ["caller", "Can I book for Tuesday?", "hearing"],
  ["assistant", "I can't book by voice yet. Please use the booking form further down this page.", "speaking"],
];

export interface RealtimeDeps {
  createPeerConnection: () => RTCPeerConnection;
  getMicrophone: () => Promise<MediaStream>;
  postOffer: (sdp: string, secret: string) => Promise<string>;
}

export const browserDeps: RealtimeDeps = {
  createPeerConnection: () => new RTCPeerConnection(),
  getMicrophone: () => navigator.mediaDevices.getUserMedia({ audio: true }),
  postOffer: async (sdp, secret) => {
    const response = await fetch(REALTIME_CALLS_URL, {
      method: "POST",
      body: sdp,
      headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/sdp" },
    });
    if (!response.ok) throw new Error(`Realtime connection failed with status ${response.status}`);
    return response.text();
  },
};

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
    return "Microphone access was blocked. Allow the microphone in your browser settings, or use the booking form below.";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "No microphone was found. You can use the booking form below instead.";
  }
  if (error instanceof ApiRequestError) return error.message;
  return "We couldn't connect to the voice assistant. Please try again, or use the booking form below.";
}

export default function VoiceAssistant({ deps = browserDeps }: { deps?: RealtimeDeps }) {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [mode, setMode] = useState<"test" | "live" | null>(null);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [muted, setMuted] = useState(false);
  const [notice, setNotice] = useState("");
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  const session = useRef<VoiceSession | null>(null);
  const peer = useRef<RTCPeerConnection | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const timers = useRef<number[]>([]);
  const usage = useRef<VoiceUsage>({ input_tokens: 0, output_tokens: 0, responses: 0 });
  const ending = useRef(false);

  const upsertLine = useCallback((line: TranscriptLine, append = false) => {
    setLines((current) => {
      const index = current.findIndex((item) => item.id === line.id);
      if (index === -1) return [...current, line];
      const next = [...current];
      next[index] = { ...line, text: append ? next[index].text + line.text : line.text };
      return next;
    });
  }, []);

  const release = useCallback(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    peer.current?.getSenders().forEach((sender) => sender.track?.stop());
    peer.current?.close();
    peer.current = null;
    if (audio.current) {
      audio.current.pause();
      audio.current.srcObject = null;
    }
    setSecondsLeft(null);
  }, []);

  const end = useCallback(
    (reason: VoiceEndReason, message = "") => {
      if (ending.current) return;
      ending.current = true;
      release();
      const current = session.current;
      session.current = null;
      if (current) endVoiceSession(current, reason, current.mode === "live" ? usage.current : null);
      setMuted(false);
      setNotice(message);
      setStatus(reason === "error" || reason === "mic_denied" ? "error" : "ended");
    },
    [release],
  );

  // Stop everything if the visitor leaves the page or this section unmounts mid-call.
  useEffect(() => {
    // Only a call in progress needs ending. React can run this cleanup without one.
    const endIfActive = () => {
      if (session.current || peer.current || stream.current) end("page_closed");
    };
    window.addEventListener("pagehide", endIfActive);
    return () => {
      window.removeEventListener("pagehide", endIfActive);
      endIfActive();
    };
  }, [end]);

  function startCountdown(seconds: number) {
    const deadline = Date.now() + seconds * 1000;
    const tick = () => {
      const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      setSecondsLeft(left);
      if (left === 0) {
        end("timeout", "The call reached its time limit. You can start a new one or use the booking form.");
      } else {
        timers.current.push(window.setTimeout(tick, 1000));
      }
    };
    tick();
  }

  function handleEvent(raw: string) {
    let event: { type?: string; [key: string]: unknown };
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    switch (event.type) {
      case "input_audio_buffer.speech_started":
        setStatus("hearing");
        break;
      case "input_audio_buffer.speech_stopped":
        setStatus("thinking");
        break;
      case "output_audio_buffer.started":
        setStatus("speaking");
        break;
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared":
        setStatus("listening");
        break;
      case "conversation.item.input_audio_transcription.completed":
        upsertLine({ id: `caller-${event.item_id}`, speaker: "caller", text: String(event.transcript ?? "").trim(), final: true });
        break;
      case "response.output_audio_transcript.delta":
        upsertLine({ id: `assistant-${event.item_id}`, speaker: "assistant", text: String(event.delta ?? ""), final: false }, true);
        break;
      case "response.output_audio_transcript.done":
        upsertLine({ id: `assistant-${event.item_id}`, speaker: "assistant", text: String(event.transcript ?? ""), final: true });
        break;
      case "response.done": {
        const responseUsage = (event.response as { usage?: { input_tokens?: number; output_tokens?: number } } | undefined)?.usage;
        usage.current = {
          input_tokens: usage.current.input_tokens + (responseUsage?.input_tokens ?? 0),
          output_tokens: usage.current.output_tokens + (responseUsage?.output_tokens ?? 0),
          responses: usage.current.responses + 1,
        };
        break;
      }
      case "error":
        // Provider errors arrive here. Most are recoverable, so keep the call open.
        console.warn("Voice session error event", (event.error as { code?: string } | undefined)?.code);
        break;
    }
  }

  async function connectLive(current: VoiceSession) {
    const microphone = await deps.getMicrophone();
    if (ending.current) {
      microphone.getTracks().forEach((track) => track.stop());
      return;
    }
    stream.current = microphone;
    const connection = deps.createPeerConnection();
    peer.current = connection;
    const player = audio.current ?? new Audio();
    player.autoplay = true;
    audio.current = player;
    connection.ontrack = (event) => {
      player.srcObject = event.streams[0];
    };
    connection.onconnectionstatechange = () => {
      if (connection.connectionState === "failed" || connection.connectionState === "disconnected") {
        end("disconnected", "The connection dropped. You can start a new call or use the booking form.");
      }
    };
    microphone.getTracks().forEach((track) => connection.addTrack(track, microphone));
    const channel = connection.createDataChannel("oai-events");
    channel.addEventListener("message", (event) => handleEvent(String(event.data)));
    channel.addEventListener("open", () => {
      setStatus("listening");
      // The instructions ask the assistant to greet first.
      channel.send(JSON.stringify({ type: "response.create" }));
    });
    const offer = await connection.createOffer();
    await connection.setLocalDescription(offer);
    const answer = await deps.postOffer(offer.sdp ?? "", current.client_secret ?? "");
    if (ending.current) return;
    await connection.setRemoteDescription({ type: "answer", sdp: answer });
  }

  function runTestScript() {
    setStatus("listening");
    let delay = 600;
    TEST_SCRIPT.forEach(([speaker, text, lineStatus], index) => {
      timers.current.push(
        window.setTimeout(() => {
          setStatus(lineStatus);
          upsertLine({ id: `test-${index}`, speaker, text, final: true });
        }, delay),
      );
      delay += 1800;
      timers.current.push(window.setTimeout(() => setStatus("listening"), delay - 400));
    });
  }

  async function startCall() {
    ending.current = false;
    usage.current = { input_tokens: 0, output_tokens: 0, responses: 0 };
    setLines([]);
    setNotice("");
    setMuted(false);
    setStatus("starting");
    try {
      const current = await startVoiceSession();
      session.current = current;
      setMode(current.mode);
      startCountdown(current.max_duration_seconds);
      if (current.mode === "test") runTestScript();
      else await connectLive(current);
    } catch (error) {
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      if (session.current) {
        end(denied ? "mic_denied" : "error", errorMessage(error));
      } else {
        ending.current = true;
        release();
        setNotice(errorMessage(error));
        setStatus("error");
      }
    }
  }

  function toggleMute() {
    const next = !muted;
    stream.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMuted(next);
  }

  const active = !["idle", "ended", "error"].includes(status);

  return (
    <div className="voice-panel">
      {mode === "test" && (
        <p className="voice-test-label" role="note">
          <strong>Test mode.</strong> This is a scripted preview. No microphone is used, no AI is
          involved, and nothing is recorded.
        </p>
      )}
      <div className="voice-controls">
        <p className={`voice-status voice-status-${status}`} aria-live="polite">
          <span className="voice-dot" aria-hidden="true" />
          {STATUS_TEXT[status]}
          {muted && active ? ", microphone muted" : ""}
          {secondsLeft !== null && active && (
            <span className="voice-timer">
              {" "}
              ({Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")} left)
            </span>
          )}
        </p>
        <div className="actions">
          {!active ? (
            <button type="button" className="button button-primary" onClick={startCall}>
              {status === "idle" ? "Start talking" : "Start a new call"}
            </button>
          ) : (
            <>
              {mode === "live" && (
                <button type="button" className="button button-secondary" aria-pressed={muted} onClick={toggleMute}>
                  {muted ? "Unmute" : "Mute"}
                </button>
              )}
              <button type="button" className="button button-primary" onClick={() => end("user_ended")}>
                End call
              </button>
            </>
          )}
        </div>
      </div>
      {notice && (
        <p className={status === "error" ? "form-alert" : "form-info"} role={status === "error" ? "alert" : "status"}>
          {notice}
        </p>
      )}
      {lines.length > 0 && (
        <ol className="transcript" aria-label="Conversation transcript" aria-live="polite">
          {lines.map((line) => (
            <li key={line.id} className={`transcript-${line.speaker}`}>
              <span className="transcript-speaker">{line.speaker === "assistant" ? "Assistant" : "You"}</span>
              {line.text}
            </li>
          ))}
        </ol>
      )}
      <p className="muted voice-fineprint">
        The assistant answers from this demo business's details and can't book yet, so please use
        the booking form for appointments. Audio is not recorded. The transcript stays in your
        browser and disappears when you leave.
      </p>
    </div>
  );
}
