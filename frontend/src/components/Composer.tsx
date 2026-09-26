import clsx from "clsx";
import {
  ArrowUp, AudioLines, Brain, Camera, FileText, Headphones, Loader2, MessagesSquare, Mic, MonitorUp, Paperclip, Square, X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { get, post } from "../api/client";
import type { Attachment, Conversation, Project } from "../api/types";
import { AUDIO_MIME, captureScreen, extFor, mediaSupport, micStream, startRecording, type Recording } from "../lib/media";
import { useFeature } from "../lib/capabilities";
import { VoiceClient } from "../lib/voice";
import { useChat } from "../store/chat";
import { useDraft } from "../store/draft";
import { useUi } from "../store/ui";
import { CameraDialog, useElapsed, Waveform } from "./Capture";
import { DraftChip } from "./AttachmentView";

type RecordMode = null | "dictation" | "voice";

interface MenuItem {
  key: string;
  label: string;
  hint?: string;
  icon?: typeof FileText;
  run: () => void;
}

const SLASH = [
  { cmd: "/think", hint: "Toggle extended thinking for the next message" },
  { cmd: "/model", hint: "Choose the model for this chat" },
  { cmd: "/style", hint: "Choose a response style" },
  { cmd: "/project", hint: "Move this chat into a project" },
];

export function Composer() {
  const { text, attachments, thinking, setText, setThinking, addFiles, addExisting, remove, clear, registerFocus } = useDraft();
  const { send, stop, sending, current, models, incognito, setConversationFields, showToast } = useChat();
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [recordMode, setRecordMode] = useState<RecordMode>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [camera, setCamera] = useState(false);
  const [menu, setMenu] = useState<MenuItem[] | null>(null);
  const [menuIndex, setMenuIndex] = useState(0);
  const pttTimer = useRef<number | null>(null);
  const pttActive = useRef(false);
  const [dictStart, setDictStart] = useState<number | null>(null);
  const elapsed = useElapsed(recording?.startedAt ?? dictStart);
  const support = useMemo(mediaSupport, []);
  const hears = useFeature("voice_messages");
  const uploading = attachments.some((a) => !a.uploaded && !a.error);
  const ready = attachments.filter((a) => a.uploaded);
  const canSend = !sending && !uploading && (text.trim().length > 0 || ready.length > 0) && !recording && recordMode !== "dictation";

  useEffect(() => registerFocus(() => ta.current?.focus()), [registerFocus]);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 280)}px`;
  }, [text]);

  const submit = () => {
    if (!canSend) return;
    const atts = ready.map((a) => a.uploaded!) as Attachment[];
    const t = text.trim();
    clear();
    setThinking(false);
    send(t, atts, { thinking });
  };

  // --- voice message (audio attachment, MediaRecorder) or live dictation (streamed to /api/voice) ---
  const dictation = useRef<{ client: VoiceClient; base: string; committed: string } | null>(null);
  const active = !!recording || recordMode === "dictation";

  const micError = (e: unknown) => {
    const err = e as DOMException;
    showToast(err.name === "NotAllowedError"
      ? "Microphone access was denied. Allow it in your browser and in System Settings → Privacy & Security → Microphone."
      : `Microphone unavailable: ${err.message}`);
  };
  const join = (...parts: string[]) => parts.map((x) => x.trim()).filter(Boolean).join(" ");

  const startDictation = async () => {
    if (active) return;
    const d = { client: null as unknown as VoiceClient, base: useDraft.getState().text, committed: "" };
    d.client = new VoiceClient({
      mode: "dictation",
      onEvent: (e) => {
        if (e.type === "partial_transcript") setText(join(d.base, d.committed, e.text));
        if (e.type === "final_transcript") {
          d.committed = join(d.committed, e.text);
          setText(join(d.base, d.committed));
        }
        if (e.type === "state" && e.state === "closed") {
          setText(join(d.base, d.committed));
          setTranscribing(false);
        }
      },
    });
    dictation.current = d;
    setRecordMode("dictation");
    setDictStart(performance.now());
    try {
      await d.client.start();
    } catch (e) {
      dictation.current = null;
      setRecordMode(null);
      d.client.close();
      micError(e);
    }
  };
  const stopDictation = (discard = false) => {
    const d = dictation.current;
    dictation.current = null;
    setRecordMode(null);
    setDictStart(null);
    if (!d) return;
    if (discard) {
      setText(d.base);
      d.client.close();
      return;
    }
    setTranscribing(true);
    d.client.finish(); // server finalizes the last utterance, sends final_transcript, then closes
    setTimeout(() => { d.client.close(); setTranscribing(false); }, 4000);
    ta.current?.focus();
  };

  const startVoiceMessage = async () => {
    if (active) return;
    post("/models/preload-listener").catch(() => undefined); // warm Gemma while you talk
    try {
      setRecording(startRecording(await micStream(), AUDIO_MIME()));
      setRecordMode("voice");
    } catch (e) {
      micError(e);
    }
  };
  const stopVoiceMessage = async (discard = false) => {
    const rec = recording;
    setRecording(null);
    setRecordMode(null);
    if (!rec) return;
    if (discard) return rec.cancel();
    const blob = await rec.stop();
    if (blob.size < 2000) return; // accidental tap
    addFiles([new File([blob], `voice-message-${Date.now()}.${extFor(rec.mime, "webm")}`, { type: blob.type })], "voice_message");
  };
  const stopRec = (discard = false) => (recordMode === "dictation" ? stopDictation(discard) : stopVoiceMessage(discard));

  // Mic button: tap toggles dictation; press and hold is push-to-talk.
  const micDown = () => {
    if (active) return;
    pttTimer.current = window.setTimeout(() => {
      pttActive.current = true;
      startDictation();
    }, 280);
  };
  const micUp = () => {
    if (pttTimer.current) clearTimeout(pttTimer.current);
    pttTimer.current = null;
    if (pttActive.current) {
      pttActive.current = false;
      stopDictation();
    } else if (recordMode === "dictation") {
      stopDictation();
    } else if (!active) {
      startDictation();
    }
  };

  const screenshot = async () => {
    try {
      const blob = await captureScreen();
      addFiles([new File([blob], `screenshot-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.png`, { type: "image/png" })], "screenshot");
    } catch (e) {
      const err = e as DOMException;
      if (err.name !== "NotAllowedError" && err.name !== "AbortError") showToast(`Screen capture failed: ${err.message}`);
    }
  };

  // --- slash commands and @-mentions --------------------------------------------------------
  const closeMenu = () => {
    setMenu(null);
    setMenuIndex(0);
  };
  const runSlash = async (cmd: string) => {
    setText("");
    closeMenu();
    if (cmd === "/think") {
      setThinking(!useDraft.getState().thinking);
      return;
    }
    if (cmd === "/model") {
      setMenu((models?.llms ?? []).filter((l) => l.capabilities.tools && l.installed !== false).map((l) => ({
        key: l.id, label: l.display_name, hint: l.loaded ? "loaded" : `${l.est_memory_gb} GB`,
        run: () => { setConversationFields({ model: l.id }); closeMenu(); },
      })));
    }
    if (cmd === "/style") {
      const styles = await get<{ presets: Record<string, string>; custom: Record<string, string> }>("/styles");
      setMenu([{ key: "default", label: "Default", run: () => { setConversationFields({ style: "default" }); closeMenu(); } },
        ...[...Object.keys(styles.presets), ...Object.keys(styles.custom)].map((s) => ({
          key: s, label: s[0].toUpperCase() + s.slice(1), hint: (styles.presets[s] ?? styles.custom[s]).slice(0, 60),
          run: () => { setConversationFields({ style: s }); closeMenu(); },
        }))]);
    }
    if (cmd === "/project") {
      const projects = await get<Project[]>("/projects");
      setMenu(projects.length ? projects.map((p) => ({
        key: p.id, label: p.name, run: () => { setConversationFields({ project_id: p.id }); closeMenu(); showToast(`Moved to ${p.name}`); },
      })) : [{ key: "none", label: "No projects yet", hint: "Create one from the sidebar", run: closeMenu }]);
    }
  };

  const updateMenus = async (value: string, caret: number) => {
    if (/^\/\w*$/.test(value)) {
      const q = value.toLowerCase();
      const items = SLASH.filter((s) => s.cmd.startsWith(q)).map((s) => ({ key: s.cmd, label: s.cmd, hint: s.hint, run: () => runSlash(s.cmd) }));
      setMenu(items.length ? items : null);
      setMenuIndex(0);
      return;
    }
    const m = /(^|\s)@([\w.-]{0,40})$/.exec(value.slice(0, caret));
    if (!m) return setMenu(null);
    const q = m[2].toLowerCase();
    const replaceMention = (insert: string) => {
      const start = caret - m[2].length - 1;
      setText(value.slice(0, start) + insert + value.slice(caret));
      closeMenu();
    };
    const files: Attachment[] = [];
    if (current) {
      files.push(...Object.values(current.attachments));
      if (current.conversation.project_id) {
        const p = await get<{ files: Attachment[] }>(`/projects/${current.conversation.project_id}`).catch(() => ({ files: [] }));
        files.push(...p.files);
      }
    }
    const chats = await get<Conversation[]>(`/conversations?limit=8${q ? `&q=${encodeURIComponent(q)}` : ""}`).catch(() => []);
    const items: MenuItem[] = [
      ...files.filter((f) => f.filename.toLowerCase().includes(q)).slice(0, 6).map((f) => ({
        key: f.id, label: f.filename, hint: "file", icon: FileText,
        run: () => { addExisting(f); replaceMention(""); },
      })),
      ...chats.filter((c) => c.id !== current?.conversation.id && c.title).slice(0, 6).map((c) => ({
        key: c.id, label: c.title!, hint: "past chat", icon: MessagesSquare,
        run: () => replaceMention(`(see my earlier chat “${c.title}”) `),
      })),
    ];
    setMenu(items.length ? items : null);
    setMenuIndex(0);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menu) {
      if (e.key === "ArrowDown") { e.preventDefault(); setMenuIndex((i) => (i + 1) % menu.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setMenuIndex((i) => (i - 1 + menu.length) % menu.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); menu[menuIndex]?.run(); return; }
      if (e.key === "Escape") { e.preventDefault(); closeMenu(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const modelName = models?.llms.find((l) => l.id === (current?.conversation.model ?? models.defaults.llm))?.display_name;

  return (
    <div className="no-print mx-auto w-full max-w-3xl px-3 pb-3 md:px-6 md:pb-5" style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}>
      <div className={clsx("relative rounded-2xl border bg-surface shadow-card transition-colors", incognito ? "border-dashed border-muted" : "border-line", "focus-within:border-accent/60")}>
        {menu && (
          <ul role="listbox" aria-label="Suggestions" className="absolute bottom-full left-2 z-30 mb-2 max-h-72 w-80 overflow-auto rounded-xl border border-line bg-surface p-1 shadow-card">
            {menu.map((item, i) => {
              const Icon = item.icon;
              return (
                <li key={item.key} role="option" aria-selected={i === menuIndex}>
                  <button type="button" onMouseDown={(e) => { e.preventDefault(); item.run(); }} onMouseEnter={() => setMenuIndex(i)}
                    className={clsx("flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm", i === menuIndex && "bg-surface-2")}>
                    {Icon && <Icon size={14} className="shrink-0 text-muted" />}
                    <span className="shrink-0 font-medium">{item.label}</span>
                    {item.hint && <span className="min-w-0 truncate text-xs text-faint">{item.hint}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {attachments.map((d) => <DraftChip key={d.key} d={d} onRemove={() => remove(d.key)} />)}
          </div>
        )}

        {recordMode === "dictation" && (
          <div className="flex items-center gap-3 px-4 pt-3" role="status" aria-label="Dictating">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-danger pulse" />
            <span className="shrink-0 text-sm text-muted">Listening · {elapsed.toFixed(0)}s</span>
            <Waveform level={() => VoiceClient.level(dictation.current?.client.micAnalyser ?? null)} className="min-w-0 flex-1" />
            <button type="button" onClick={() => stopDictation(true)} className="rounded-md p-1.5 text-muted hover:bg-surface-2" aria-label="Discard dictation"><X size={16} /></button>
            <button type="button" onClick={() => stopDictation()} className="rounded-full bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">Done</button>
          </div>
        )}
        {recording ? (
          <div className="flex items-center gap-3 px-4 py-3" role="status" aria-label={recordMode === "voice" ? "Recording voice message" : "Dictating"}>
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-danger pulse" />
            <span className="shrink-0 text-sm text-muted">{recordMode === "voice" ? "Voice message" : "Listening"} · {elapsed.toFixed(0)}s</span>
            <Waveform level={recording.level} className="min-w-0 flex-1" />
            <button type="button" onClick={() => stopRec(true)} className="rounded-md p-1.5 text-muted hover:bg-surface-2" aria-label="Discard recording"><X size={16} /></button>
            <button type="button" onClick={() => stopRec()} className="rounded-full bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">
              {recordMode === "voice" ? "Attach" : "Done"}
            </button>
          </div>
        ) : (
          <textarea
            ref={ta}
            value={text}
            rows={1}
            placeholder={transcribing ? "Transcribing…" : incognito ? "Incognito chat: not saved, no memory" : "Message the assistant"}
            aria-label="Message"
            onChange={(e) => { setText(e.target.value); updateMenus(e.target.value, e.target.selectionStart); }}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(closeMenu, 150)}
            onPaste={(e) => {
              const files = [...e.clipboardData.files];
              if (files.length) {
                e.preventDefault();
                addFiles(files.map((f, i) => (f.name && f.name !== "image.png" ? f
                  : new File([f], `pasted-${Date.now()}-${i}.${f.type.split("/")[1] || "png"}`, { type: f.type }))), "paste");
              }
            }}
            className="block max-h-72 w-full resize-none bg-transparent px-4 pt-3 pb-1 outline-none placeholder:text-faint"
          />
        )}

        <div className="flex items-center gap-0.5 px-2 pb-2">
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => { addFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
          <ToolbarButton label="Attach files" onClick={() => fileInput.current?.click()}><Paperclip size={17} /></ToolbarButton>
          {support.screen && <ToolbarButton label="Capture screen or window" onClick={screenshot}><MonitorUp size={17} /></ToolbarButton>}
          {support.camera && <ToolbarButton label="Camera: photo or video clip" onClick={() => setCamera(true)}><Camera size={17} /></ToolbarButton>}
          {support.mic && (
            <ToolbarButton label={hears?.available === false ? `Voice messages aren't available: ${hears.reason}` : "Record a voice message"}
              disabled={hears?.available === false} onClick={() => (recording ? stopVoiceMessage() : startVoiceMessage())} active={recordMode === "voice"}>
              <AudioLines size={17} />
            </ToolbarButton>
          )}
          <ToolbarButton label={thinking ? "Extended thinking on" : "Extended thinking off"} onClick={() => setThinking(!thinking)} active={thinking}>
            <Brain size={17} />{thinking && <span className="ml-1 text-xs">Think</span>}
          </ToolbarButton>
          <span className="flex-1" />
          {modelName && <span className="mr-2 hidden truncate text-xs text-faint sm:inline">{modelName}</span>}
          {support.mic && (
            <button type="button" aria-label={recordMode === "dictation" ? "Stop dictation" : "Dictate (tap, or hold to talk)"}
              title="Dictate: tap to start/stop, or press and hold"
              onPointerDown={micDown} onPointerUp={micUp} onPointerLeave={() => pttActive.current && micUp()}
              className={clsx("mr-1 rounded-full p-2 hover:bg-surface-2", recordMode === "dictation" ? "bg-danger-soft text-danger" : "text-muted")}>
              {transcribing ? <Loader2 size={17} className="animate-spin" /> : <Mic size={17} />}
            </button>
          )}
          {support.mic && !sending && !text.trim() && !attachments.length && (
            <button type="button" onClick={() => useUi.getState().setVoice(true)} aria-label="Voice mode (⌘⇧V)" title="Voice mode: talk with the assistant (⌘⇧V)"
              className="mr-1 flex items-center gap-1.5 rounded-full bg-surface-2 px-3 py-1.5 text-sm text-fg hover:bg-surface-3">
              <Headphones size={15} /> Talk
            </button>
          )}
          {sending ? (
            <button type="button" onClick={stop} aria-label="Stop generating (Esc)" className="rounded-full bg-fg p-2 text-bg hover:opacity-80"><Square size={16} /></button>
          ) : (
            <button type="button" onClick={submit} disabled={!canSend} aria-label="Send message"
              className="rounded-full bg-accent p-2 text-on-accent transition-opacity hover:bg-accent-strong disabled:opacity-30">
              {uploading ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
            </button>
          )}
        </div>
      </div>
      <p className="mt-1.5 text-center text-[11px] text-faint">
        {incognito ? "Incognito: this chat disappears when you close it." : "Runs entirely on this Mac, even offline. Replies can be wrong; check important facts."}
      </p>
      {camera && <CameraDialog onClose={() => setCamera(false)} onCapture={(f, src) => addFiles([f], src)} />}
    </div>
  );
}

function ToolbarButton({ label, onClick, children, active, disabled }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} aria-pressed={active} disabled={disabled}
      className={clsx("flex items-center rounded-lg p-2 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent",
        active ? "bg-accent-soft text-accent" : "text-muted hover:text-fg")}>
      {children}
    </button>
  );
}
