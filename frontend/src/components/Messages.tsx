import clsx from "clsx";
import {
  AlertTriangle, Check, ChevronLeft, ChevronRight, Copy, FileCode2, Loader2, PanelsTopLeft, Pencil, Pin,
  PinOff, RefreshCw, Square, Volume2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiUrl, getToken, patch } from "../api/client";
import type { Artifact, Attachment, Citation, Message } from "../api/types";
import { blocksToView, type LiveBlock, type LiveReply } from "../lib/live";
import { buildPath, type PathEntry } from "../lib/tree";
import { useChat, type PendingUser } from "../store/chat";
import { useUi } from "../store/ui";
import { SentAttachment } from "./AttachmentView";
import { SourcesList } from "./Citations";
import { Markdown, useCopy } from "./Markdown";
import { PromptDebugModal } from "./PromptDebug";
import { ThinkingBlock, ToolCard } from "./ToolCard";

function VersionNav({ entry }: { entry: PathEntry }) {
  const switchVersion = useChat((s) => s.switchVersion);
  const sending = useChat((s) => s.sending);
  if (entry.siblings.length < 2) return null;
  const go = (d: number) => switchVersion(entry.siblings[entry.index + d].id);
  return (
    <span className="inline-flex items-center gap-0.5 text-xs text-muted" aria-label="Versions">
      <button type="button" disabled={entry.index === 0 || sending} onClick={() => go(-1)} className="rounded p-0.5 hover:bg-surface-2 disabled:opacity-30" aria-label="Previous version"><ChevronLeft size={14} /></button>
      <span className="tabular-nums">{entry.index + 1} / {entry.siblings.length}</span>
      <button type="button" disabled={entry.index === entry.siblings.length - 1 || sending} onClick={() => go(1)} className="rounded p-0.5 hover:bg-surface-2 disabled:opacity-30" aria-label="Next version"><ChevronRight size={14} /></button>
    </span>
  );
}

function IconButton({ label, onClick, children, active }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
      className={clsx("rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg", active && "text-accent")}>
      {children}
    </button>
  );
}

function UserMessage({ entry, attachments, media }: { entry: PathEntry; attachments: Record<string, Attachment>; media?: LiveReply["media"] }) {
  const m = entry.message;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(m.content);
  const send = useChat((s) => s.send);
  const sending = useChat((s) => s.sending);
  const [copied, copy] = useCopy();
  const atts = m.attachment_ids.map((id) => attachments[id]).filter(Boolean);
  const submitEdit = () => {
    if (!draft.trim() && !atts.length) return;
    setEditing(false);
    send(draft, atts, { parentId: m.parent_id ?? "" });
  };
  return (
    <div className="group flex flex-col items-end gap-1.5" data-testid="user-message">
      {atts.length > 0 && (
        <div className="flex max-w-full flex-wrap justify-end gap-2">
          {atts.map((a) => <SentAttachment key={a.id} att={a} progress={media?.[a.id]} />)}
        </div>
      )}
      {editing ? (
        <div className="w-full max-w-[85%] rounded-2xl border border-line bg-surface p-2">
          <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={Math.min(10, draft.split("\n").length + 1)}
            aria-label="Edit message"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitEdit(); }
              if (e.key === "Escape") setEditing(false);
            }}
            className="w-full resize-none bg-transparent px-2 py-1 outline-none" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setEditing(false)} className="rounded-md px-3 py-1 text-sm hover:bg-surface-2">Cancel</button>
            <button type="button" onClick={submitEdit} className="rounded-md bg-accent px-3 py-1 text-sm font-medium text-on-accent">Send</button>
          </div>
        </div>
      ) : (
        m.content && <div className="max-w-[85%] rounded-2xl bg-bubble px-4 py-2.5 whitespace-pre-wrap">{m.content}</div>
      )}
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 max-md:opacity-100">
        <VersionNav entry={entry} />
        {!editing && !sending && <IconButton label="Edit message" onClick={() => { setDraft(m.content); setEditing(true); }}><Pencil size={14} /></IconButton>}
        {m.content && <IconButton label="Copy message" onClick={() => copy(m.content)}>{copied ? <Check size={14} /> : <Copy size={14} />}</IconButton>}
      </div>
    </div>
  );
}

function artifactsFromBlocks(m: Message): Artifact[] {
  return m.blocks.flatMap((b) => (b.type === "tool_result" && b.name === "create_artifact" && b.ok && b.data?.id
    ? [b.data as unknown as Artifact] : []));
}

function speakable(md: string) {
  return md.replace(/```[\s\S]*?```/g, " (code omitted) ").replace(/`([^`]+)`/g, "$1").replace(/\[(\d+)\]/g, "")
    .replace(/[*_#>|]/g, "").replace(/\$\$?[^$]+\$\$?/g, " (formula) ").replace(/\n{2,}/g, ".\n");
}

let currentAudio: HTMLAudioElement | null = null;

function ReadAloud({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const start = async () => {
    if (state !== "idle") {
      currentAudio?.pause();
      currentAudio = null;
      setState("idle");
      return;
    }
    currentAudio?.pause();
    setState("loading");
    try {
      const t = getToken();
      const res = await fetch(apiUrl("/tts"), { method: "POST", headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) }, body: JSON.stringify({ text: speakable(text).slice(0, 8000) }) });
      if (!res.ok) throw new Error(await res.text());
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      currentAudio = audio;
      audio.onended = () => setState("idle");
      await audio.play();
      setState("playing");
    } catch {
      setState("idle");
    }
  };
  return (
    <IconButton label={state === "idle" ? "Read aloud" : "Stop reading"} onClick={start} active={state !== "idle"}>
      {state === "loading" ? <Loader2 size={14} className="animate-spin" /> : state === "playing" ? <Square size={14} /> : <Volume2 size={14} />}
    </IconButton>
  );
}

function RetryMenu({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const models = useChat((s) => s.models);
  const regenerate = useChat((s) => s.regenerate);
  const sending = useChat((s) => s.sending);
  if (sending) return null;
  return (
    <span className="relative">
      <IconButton label="Retry" onClick={() => setOpen((v) => !v)}><RefreshCw size={14} /></IconButton>
      {open && (
        <div role="menu" className="absolute bottom-full left-0 z-20 mb-1 w-60 rounded-lg border border-line bg-surface p-1 text-sm shadow-card"
          onMouseLeave={() => setOpen(false)}>
          <button role="menuitem" type="button" className="w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
            onClick={() => { setOpen(false); regenerate(messageId); }}>Retry</button>
          <button role="menuitem" type="button" className="w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
            onClick={() => { setOpen(false); regenerate(messageId, { thinking: true }); }}>Retry with extended thinking</button>
          <div className="my-1 border-t border-line" />
          <div className="px-2 py-1 text-xs text-faint">Retry with model</div>
          {models?.llms.filter((l) => l.capabilities.tools && l.installed !== false).map((l) => (
            <button key={l.id} role="menuitem" type="button" className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
              onClick={() => { setOpen(false); regenerate(messageId, { model: l.id }); }}>
              <span>{l.display_name}</span>{l.loaded && <span className="text-xs text-accent">loaded</span>}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

function renderBlocks(blocks: LiveBlock[], citations: Citation[], streaming: boolean, interactive: boolean) {
  const lastText = [...blocks].reverse().find((b) => b.kind === "text");
  return blocks.map((b, i) => {
    if (b.kind === "thinking") return <ThinkingBlock key={i} text={b.text} done={b.done} />;
    if (b.kind === "tool") return <ToolCard key={b.tool.id} tool={b.tool} interactive={interactive} />;
    return <Markdown key={i} text={b.text} citations={citations} streaming={streaming && b === lastText && b === blocks[blocks.length - 1]} />;
  });
}

function AssistantMessage({ entry, onDebug }: { entry: PathEntry; onDebug: (id: string) => void }) {
  const m = entry.message;
  const [copied, copy] = useCopy();
  const openArtifact = useUi((s) => s.openArtifact);
  const blocks = useMemo(() => blocksToView(m.blocks ?? [], m.content), [m.blocks, m.content]);
  const artifacts = useMemo(() => artifactsFromBlocks(m), [m]);
  const togglePin = async () => {
    await patch(`/messages/${m.id}`, { pinned: !m.pinned });
    useChat.getState().refreshCurrent();
  };
  return (
    <div className="group" data-testid="assistant-message">
      {renderBlocks(blocks, m.citations, false, false)}
      {artifacts.map((a) => <ArtifactCard key={`${a.id}-${a.version}`} artifact={a} onOpen={() => openArtifact(a)} />)}
      {m.status === "stopped" && <div className="mt-1 text-xs text-faint">Stopped</div>}
      {m.status === "error" && <div className="mt-1 flex items-center gap-1 text-xs text-danger"><AlertTriangle size={12} /> The reply failed. Retry to try again.</div>}
      {m.citations.length > 0 && <SourcesList citations={m.citations} />}
      <div className="mt-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 max-md:opacity-100">
        <VersionNav entry={entry} />
        <IconButton label="Copy reply" onClick={() => copy(m.content)}>{copied ? <Check size={14} /> : <Copy size={14} />}</IconButton>
        <RetryMenu messageId={m.id} />
        {m.content && <ReadAloud text={m.content} />}
        <IconButton label={m.pinned ? "Unpin (allow summarizing)" : "Pin (never summarize)"} onClick={togglePin} active={!!m.pinned}>
          {m.pinned ? <PinOff size={14} /> : <Pin size={14} />}
        </IconButton>
        <IconButton label="Show the prompt sent to the model" onClick={() => onDebug(m.id)}><FileCode2 size={14} /></IconButton>
        {m.model && <span className="ml-1 text-xs text-faint">{m.model}{m.usage?.decode_tok_s ? ` · ${Math.round(m.usage.decode_tok_s)} tok/s` : ""}</span>}
      </div>
    </div>
  );
}

function ArtifactCard({ artifact, onOpen }: { artifact: Artifact; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} data-testid="artifact-card"
      className="my-2 flex w-full max-w-md items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 text-left hover:bg-surface-2">
      <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent-soft text-accent"><PanelsTopLeft size={18} /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{artifact.title}</span>
        <span className="text-xs text-muted">{artifact.type.toUpperCase()} · version {artifact.version ?? artifact.latest ?? 1}</span>
      </span>
    </button>
  );
}

function LiveAssistant({ live }: { live: LiveReply }) {
  const openArtifact = useUi((s) => s.openArtifact);
  const shown = useRef<string>("");
  useEffect(() => {
    // Open the panel as soon as the assistant creates or updates an artifact.
    const a = live.artifacts[live.artifacts.length - 1];
    const key = a ? `${a.id}:${a.version}` : "";
    if (a && key !== shown.current) {
      shown.current = key;
      openArtifact(a);
    }
  }, [live.artifacts, openArtifact]);
  const empty = live.blocks.length === 0;
  return (
    <div data-testid="assistant-message" aria-busy={!live.done}>
      {live.status && (
        <div className="mb-2 flex items-center gap-2 text-sm text-muted" role="status">
          <Loader2 size={14} className="animate-spin" /> {live.status.message}
        </div>
      )}
      {empty && !live.status && !live.error && <div className="flex gap-1 py-2" aria-label="Waiting for reply"><span className="h-2 w-2 rounded-full bg-faint pulse" /><span className="h-2 w-2 rounded-full bg-faint pulse [animation-delay:.2s]" /><span className="h-2 w-2 rounded-full bg-faint pulse [animation-delay:.4s]" /></div>}
      {renderBlocks(live.blocks, live.citations, !live.done, true)}
      {live.artifacts.map((a) => <ArtifactCard key={`${a.id}-${a.version}`} artifact={a} onOpen={() => openArtifact(a)} />)}
      {Object.entries(live.warnings).map(([id, ws]) => ws.map((w) => (
        <div key={id + w} className="my-1 flex items-center gap-1.5 text-xs text-warn"><AlertTriangle size={12} />{w}</div>
      )))}
      {live.error && <div className="my-2 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">{live.error}</div>}
      {live.citations.length > 0 && live.done && <SourcesList citations={live.citations} />}
    </div>
  );
}

function PendingUserBubble({ p }: { p: PendingUser }) {
  return (
    <div className="flex flex-col items-end gap-1.5 opacity-90">
      {p.attachments.length > 0 && <div className="flex flex-wrap justify-end gap-2">{p.attachments.map((a) => <SentAttachment key={a.id} att={a} />)}</div>}
      {p.text && <div className="max-w-[85%] rounded-2xl bg-bubble px-4 py-2.5 whitespace-pre-wrap">{p.text}</div>}
    </div>
  );
}

export function MessageList() {
  const current = useChat((s) => s.current);
  const live = useChat((s) => s.live);
  const pendingUser = useChat((s) => s.pendingUser);
  const [debugId, setDebugId] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  let path = current ? buildPath(current.messages, current.active_path) : [];
  // While a turn streams, the live view owns the new messages: hide their stored copies (which
  // appear once the chat reloads mid-stream), and anything the new branch replaces.
  const cutAt = (id: string | null | undefined, keep: boolean) => {
    const i = id ? path.findIndex((e) => e.message.id === id) : -1;
    if (i >= 0) path = path.slice(0, keep ? i + 1 : i);
  };
  if (live || pendingUser) {
    if (pendingUser && pendingUser.parentId !== undefined) {
      if (pendingUser.parentId) cutAt(pendingUser.parentId, true);
      else path = [];
    }
    if (pendingUser) cutAt(live?.userMessageId, false);
    cutAt(live?.assistantId, false);
  }
  const lastUserIdx = path.length - 1;

  useEffect(() => {
    if (stick.current) bottom.current?.scrollIntoView({ block: "end" });
  });

  return (
    <div ref={scroller} className="print-full flex-1 overflow-y-auto" role="log" aria-label="Conversation" aria-live="off"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6 md:px-6">
        {current?.conversation.title && (
          <h1 className="print-only mb-2 text-xl font-semibold">{current.conversation.title}
            <span className="block text-xs font-normal text-muted">Exported {new Date().toLocaleString()} · GoRunRun Local AI</span></h1>
        )}
        {path.map((entry, i) => entry.message.role === "user"
          ? <UserMessage key={entry.message.id} entry={entry} attachments={current!.attachments} media={i === lastUserIdx ? live?.media : undefined} />
          : <AssistantMessage key={entry.message.id} entry={entry} onDebug={setDebugId} />)}
        {pendingUser && <PendingUserBubble p={pendingUser} />}
        {live && <LiveAssistant live={live} />}
        <div ref={bottom} />
      </div>
      <div className="sr-only" aria-live="polite">{live?.done ? "Reply complete." : live?.status?.message ?? ""}</div>
      {debugId && <PromptDebugModal messageId={debugId} onClose={() => setDebugId(null)} />}
    </div>
  );
}
