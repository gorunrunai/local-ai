import clsx from "clsx";
import { Check, FileCode2, Hand, Mic, MicOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { fileUrl, get, post } from "../api/client";
import type { Artifact } from "../api/types";
import { VoiceClient, type VoiceEvent, type VoiceState } from "../lib/voice";
import { useChat } from "../store/chat";
import { useUi } from "../store/ui";
import { LavaLamp, type LavaMode } from "./LavaLamp";

interface Line {
  who: "you" | "assistant";
  text: string;
  partial?: boolean;
  interrupted?: boolean;
}

const LABEL: Record<VoiceState, string> = {
  connecting: "Connecting…",
  loading: "Getting ready…",
  listening: "Listening",
  user_speaking: "Listening…",
  processing: "Got it",
  thinking: "Thinking",
  speaking: "Speaking · talk to interrupt",
  closed: "Voice mode ended",
};

const LAVA: Record<VoiceState, LavaMode> = {
  connecting: "idle", loading: "idle", listening: "listening", user_speaking: "hearing", processing: "thinking",
  thinking: "thinking", speaking: "speaking", closed: "idle",
};

const TOOL_LABEL: Record<string, string> = {
  generate_video: "Making the video. This takes a minute or more",
  create_artifact: "Putting it on screen",
  web_search: "Searching the web",
  web_fetch: "Reading a web page",
  run_python: "Running code",
};

/** Something a tool made during the conversation, shown in voice mode (the chat is hidden behind it). */
type Made = { kind: "video" | "image"; id: string; filename: string } | { kind: "artifact"; artifact: Artifact };
interface Pending { id: string; name: string; arguments: Record<string, unknown> }

/** Full-duplex voice conversation (Phase 4). Saved to the current chat as normal messages. */
export function VoiceMode({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { ensureConversation, refreshCurrent, loadConversations, showToast } = useChat();
  const [state, setState] = useState<VoiceState>("connecting");
  const [lines, setLines] = useState<Line[]>([]);
  const [muted, setMuted] = useState(false);
  const [latency, setLatency] = useState<number | null>(null);
  const [tool, setTool] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);   // the step being loaded, first start
  const [pending, setPending] = useState<Pending | null>(null);
  const [made, setMade] = useState<Made[]>([]);
  const client = useRef<VoiceClient | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const onEvent = (e: VoiceEvent) => {
      if (e.type === "state") { setState(e.state); if (e.state !== "loading") setLoading(null); }
      else if (e.type === "loading") setLoading(e.message);
      else if (e.type === "ready") navigate(`/c/${e.conversation_id}`, { replace: true });
      else if (e.type === "partial_transcript") {
        setLines((ls) => [...ls.filter((l) => !l.partial), { who: "you", text: e.text, partial: true }]);
      } else if (e.type === "final_transcript") {
        setLines((ls) => [...ls.filter((l) => !l.partial), { who: "you", text: e.text }]);
      } else if (e.type === "assistant_delta") {
        setTool(null);
        setLines((ls) => {
          const last = ls[ls.length - 1];
          if (last?.who === "assistant" && !last.interrupted) return [...ls.slice(0, -1), { ...last, text: last.text + e.text }];
          return [...ls, { who: "assistant", text: e.text }];
        });
      } else if (e.type === "tool") setTool(TOOL_LABEL[e.name] ?? `Using ${e.name.replace(/_/g, " ")}`);
      else if (e.type === "confirm") setPending({ id: e.id, name: e.name, arguments: e.arguments });
      else if (e.type === "tool_done") {
        setPending((p) => (p?.id === e.id ? null : p));
        setTool(null);
        const media = e.files.filter((f) => f.mime.startsWith("video/") || f.mime.startsWith("image/"));
        if (e.ok && media.length) {
          setMade((m) => [...m, ...media.map((f) => ({ kind: f.mime.startsWith("video/") ? "video" as const : "image" as const, id: f.id, filename: f.filename }))]);
        }
      } else if (e.type === "artifact") {
        setMade((m) => [...m.filter((x) => x.kind !== "artifact" || x.artifact.identifier !== e.artifact.identifier), { kind: "artifact", artifact: e.artifact }]);
      }
      else if (e.type === "interrupted") {
        setLines((ls) => {
          const last = ls[ls.length - 1];
          return last?.who === "assistant" ? [...ls.slice(0, -1), { ...last, interrupted: true }] : ls;
        });
      } else if (e.type === "metrics" && e.end_of_speech_to_first_audio_ms != null && !e.final) {
        setLatency(e.end_of_speech_to_first_audio_ms);
      } else if (e.type === "turn_done") {
        setPending(null);
        setTool(null);
        refreshCurrent();
        loadConversations();
      } else if (e.type === "error") showToast(e.message);
    };
    (async () => {
      try {
        const cid = await ensureConversation();
        if (cancelled) return;
        const prefs = await get<{ voice?: { tts_voice?: string; speed?: number; vad_sensitivity?: number } }>("/settings").catch(() => ({ voice: undefined }));
        const c = new VoiceClient({ mode: "conversation", conversationId: cid, onEvent, voice: prefs.voice?.tts_voice,
          speed: prefs.voice?.speed, sensitivity: prefs.voice?.vad_sensitivity });
        client.current = c;
        await c.start();
      } catch (err) {
        const e = err as DOMException;
        showToast(e.name === "NotAllowedError"
          ? "Microphone access was denied. Allow it in your browser and in System Settings → Privacy & Security → Microphone."
          : `Voice mode couldn't start: ${e.message}`);
        onClose();
      }
    })();
    return () => {
      cancelled = true;
      client.current?.close();
      refreshCurrent();
      loadConversations();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The lava lamp follows your voice while listening and the assistant's voice while speaking.
  const level = () => {
    const c = client.current;
    if (!c) return 0;
    return stateRef.current === "speaking" ? VoiceClient.level(c.outAnalyser) : c.muted ? 0 : VoiceClient.level(c.micAnalyser);
  };
  const lava: LavaMode = muted && state !== "speaking" ? "idle" : LAVA[state];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === " " && state === "speaking") {
        e.preventDefault();
        client.current?.interrupt();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, state]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  const toggleMute = () => {
    if (!client.current) return;
    client.current.muted = !muted;
    setMuted(!muted);
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Voice mode" data-testid="voice-mode"
      className="voice-stage fixed inset-0 z-50 flex flex-col text-fg">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-sm text-muted">Voice mode{latency != null && <span className="ml-2 font-mono text-xs text-faint">last reply started {(latency / 1000).toFixed(2)} s after you stopped</span>}</span>
        <button type="button" onClick={onClose} aria-label="End voice mode (Esc)" className="rounded-full p-2 text-muted hover:bg-surface-2 hover:text-fg"><X size={20} /></button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6">
        <button type="button" onClick={() => state === "speaking" && client.current?.interrupt()}
          aria-label={state === "speaking" ? "Interrupt" : LABEL[state]} className="rounded-full">
          <LavaLamp mode={lava} level={level} size={Math.max(200, Math.min(380, window.innerWidth - 48, window.innerHeight * (made.length || pending ? 0.28 : 0.45)))} />
        </button>
        <div className="text-sm font-medium tracking-wide text-muted" aria-live="polite" data-testid="voice-state">{muted && state !== "loading" ? "Muted" : LABEL[state]}</div>
        {state === "loading" && (
          <div className="max-w-sm text-center text-sm text-muted" data-testid="voice-loading">
            <p>{loading ?? "Loading"}…</p>
            <p className="mt-1 text-xs text-faint">The first start takes up to a minute. It isn't listening yet; it will say “Listening” when it's ready.</p>
          </div>
        )}
        {tool && !pending && <div className="text-sm text-muted">{tool}…</div>}
        {pending && (
          <div role="alertdialog" aria-label="Approve tool" className="w-full max-w-md rounded-2xl border border-line bg-surface p-4 text-sm">
            <p className="font-medium">{pending.name === "generate_video" ? "Create this video?" : `Allow ${pending.name.replace(/_/g, " ")}?`}</p>
            {typeof pending.arguments.prompt === "string" && <p className="mt-1 line-clamp-3 text-muted">{pending.arguments.prompt}</p>}
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" className="rounded-full bg-surface-2 px-4 py-1.5 hover:bg-surface-3"
                onClick={() => { post(`/tool-calls/${pending.id}/decision`, { approve: false }).catch(() => undefined); setPending(null); }}>Don't allow</button>
              <button type="button" className="flex items-center gap-1.5 rounded-full bg-fg px-4 py-1.5 font-medium text-bg hover:opacity-85"
                onClick={() => { post(`/tool-calls/${pending.id}/decision`, { approve: true }).catch(() => undefined); setPending(null); }}><Check size={15} /> Allow</button>
            </div>
          </div>
        )}
        {made.length > 0 && (
          <div className="flex max-w-full gap-3 overflow-x-auto pb-1" aria-label="Made in this conversation">
            {made.map((m) => m.kind === "artifact" ? (
              <button key={m.artifact.identifier} type="button" onClick={() => { onClose(); useUi.getState().openArtifact(m.artifact); }}
                className="flex shrink-0 items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-left text-sm hover:bg-surface-2">
                <FileCode2 size={18} className="text-muted" />
                <span><span className="block font-medium">{m.artifact.title}</span><span className="text-xs text-muted">Open it (ends voice mode)</span></span>
              </button>
            ) : m.kind === "video" ? (
              <video key={m.id} src={fileUrl(m.id)} controls playsInline preload="metadata" aria-label={m.filename}
                className="max-h-[28vh] shrink-0 rounded-xl border border-line bg-black" />
            ) : (
              <img key={m.id} src={fileUrl(m.id)} alt={m.filename} className="max-h-[28vh] shrink-0 rounded-xl border border-line" />
            ))}
          </div>
        )}
      </div>

      <div ref={logRef} className="mx-auto max-h-[34vh] w-full max-w-2xl overflow-y-auto px-6" aria-label="Voice transcript" data-testid="voice-transcript">
        {lines.map((l, i) => (
          <p key={i} className={clsx("my-1.5 text-[15px]", l.who === "you" ? "text-right text-muted" : "text-fg", l.partial && "italic opacity-70")}>
            {l.text}{l.interrupted && <span className="ml-1 text-xs text-faint">(interrupted)</span>}
          </p>
        ))}
      </div>

      <div className="flex items-center justify-center gap-4 px-6 py-6" style={{ paddingBottom: "max(1.5rem, env(safe-area-inset-bottom))" }}>
        <button type="button" onClick={toggleMute} aria-pressed={muted} aria-label={muted ? "Unmute microphone" : "Mute microphone"}
          className={clsx("rounded-full p-4", muted ? "bg-danger text-white" : "bg-surface-2 text-fg hover:bg-surface-3")}>
          {muted ? <MicOff size={22} /> : <Mic size={22} />}
        </button>
        <button type="button" onClick={() => client.current?.interrupt()} disabled={state !== "speaking" && state !== "thinking"}
          aria-label="Interrupt (Space)" className="rounded-full bg-surface-2 p-4 text-fg hover:bg-surface-3 disabled:opacity-30">
          <Hand size={22} />
        </button>
        <button type="button" onClick={onClose} className="rounded-full bg-fg px-6 py-3 text-sm font-medium text-bg hover:opacity-85">End</button>
      </div>
    </div>
  );
}
