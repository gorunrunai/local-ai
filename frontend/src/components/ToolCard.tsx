import clsx from "clsx";
import {
  Brain, Check, ChevronRight, Clapperboard, Code2, Download, FileSearch, Globe, Loader2, NotebookPen, PanelsTopLeft, Search,
  ShieldAlert, ShieldX, Video, Wrench, X,
} from "lucide-react";
import { useState } from "react";
import { fileUrl } from "../api/client";
import type { ToolView } from "../lib/live";
import { useChat } from "../store/chat";

const ICONS: Record<string, typeof Wrench> = {
  web_fetch: Globe, web_search: Search, code_exec: Code2, file_read: FileSearch, file_search: FileSearch,
  memory_save: NotebookPen, memory_search: NotebookPen, conversation_search: Search,
  create_artifact: PanelsTopLeft, analyze_media: Video, generate_video: Clapperboard,
};

// [while pending/running, when finished]
const LABELS: Record<string, [string, string]> = {
  web_fetch: ["Read web page", "Read web page"], web_search: ["Search the web", "Searched the web"],
  code_exec: ["Run code", "Ran code"], file_read: ["Read file", "Read file"],
  file_search: ["Search files", "Searched files"], memory_save: ["Save to memory", "Saved to memory"],
  memory_search: ["Search memory", "Searched memory"], conversation_search: ["Search past chats", "Searched past chats"],
  create_artifact: ["Create artifact", "Artifact"], analyze_media: ["Look at media", "Looked at media"],
  generate_video: ["Create video", "Created video"],
};

function summary(t: ToolView): string {
  const a = (t.arguments ?? {}) as Record<string, unknown>;
  const d = t.data ?? {};
  switch (t.name) {
    case "web_fetch": return String(d.title ?? a.url ?? "");
    case "web_search":
    case "file_search":
    case "memory_search":
    case "conversation_search": return a.query ? `“${a.query}”` : "";
    case "memory_save": return String(a.fact ?? "");
    case "file_read": return String(a.file ?? "");
    case "create_artifact": return `${a.title ?? a.identifier ?? ""}${d.version ? ` · v${d.version}` : ""}`;
    case "analyze_media": return `${a.file ?? ""} ${a.start_s ?? ""}–${a.end_s ?? ""}s`;
    case "generate_video": {
      const secs = d.seconds ?? a.seconds ?? 4;
      return [`${Number(secs).toFixed(secs === Math.round(Number(secs)) ? 0 : 1)}s`, d.model_name ?? a.model, a.prompt ? `“${a.prompt}”` : ""]
        .filter(Boolean).join(" · ");
    }
    default: return "";
  }
}

function prettyName(name: string, done = true) {
  if (LABELS[name]) return LABELS[name][done ? 1 : 0];
  const m = /^mcp__(.+?)__(.+)$/.exec(name);
  return m ? `${m[2]} (${m[1]})` : name;
}

/** Where a video or image used to be, after Settings → Clear clutter removed it. */
function ClearedNote({ what }: { what: string }) {
  return (
    <p className="rounded-md border border-dashed border-line px-3 py-4 text-center text-xs text-muted" data-testid="cleared-note">
      This {what} was cleared to free up space (Settings → Clear clutter). Ask again to make a new one.
    </p>
  );
}

export function ToolCard({ tool, interactive }: { tool: ToolView; interactive?: boolean }) {
  const [open, setOpen] = useState(false);
  const [cleared, setCleared] = useState<Set<string>>(new Set());
  const markCleared = (id: string) => setCleared((c) => new Set(c).add(id));
  const decideTool = useChat((s) => s.decideTool);
  const Icon = ICONS[tool.name] ?? Wrench;
  const awaiting = tool.status === "awaiting_confirmation";
  const running = tool.status === "running" || tool.status === "pending";
  const code = tool.name === "code_exec" ? String((tool.arguments as { code?: string })?.code ?? "") : null;
  const images = (tool.files ?? []).filter((f) => f.id && /\.(png|jpe?g|gif|webp|svg)$/i.test(f.filename));
  const videos = (tool.files ?? []).filter((f) => f.id && /\.(mp4|mov|webm)$/i.test(f.filename));
  const p = running ? tool.progress : undefined;

  return (
    <div className={clsx("my-2 rounded-lg border text-sm", awaiting ? "border-warn bg-warn-soft" : "border-line bg-surface")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <ChevronRight size={14} className={clsx("shrink-0 text-faint transition-transform", open && "rotate-90")} />
        <Icon size={15} className="shrink-0 text-muted" />
        <span className="shrink-0 font-medium">{prettyName(tool.name, !(running || awaiting))}{running && tool.status === "running" ? "…" : ""}</span>
        <span className="min-w-0 flex-1 truncate text-muted">{summary(tool)}</span>
        {running && <Loader2 size={14} className="shrink-0 animate-spin text-muted" aria-label="Running" />}
        {tool.status === "ok" && <Check size={14} className="shrink-0 text-accent" aria-label="Succeeded" />}
        {tool.status === "error" && <X size={14} className="shrink-0 text-danger" aria-label="Failed" />}
        {tool.status === "denied" && <ShieldX size={14} className="shrink-0 text-danger" aria-label="Not run" />}
        {awaiting && <ShieldAlert size={14} className="shrink-0 text-warn" aria-label="Needs approval" />}
        {tool.duration_ms != null && <span className="shrink-0 font-mono text-xs text-faint">{(tool.duration_ms / 1000).toFixed(1)}s</span>}
      </button>

      {awaiting && (
        <div className="flex flex-wrap items-center gap-2 border-t border-warn/40 px-3 py-2">
          <span className="min-w-0 flex-1 text-xs">Allow this tool call? <span className="text-muted">({tool.reason})</span></span>
          {interactive && (
            <>
              <button type="button" onClick={() => decideTool(tool.id, false)} className="rounded-md border border-line bg-surface px-3 py-1 text-xs hover:bg-surface-2">Deny</button>
              <button type="button" onClick={() => decideTool(tool.id, true)} className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-on-accent hover:bg-accent-strong">Allow</button>
            </>
          )}
        </div>
      )}

      {p && (
        <div className="border-t border-line px-3 py-2" data-testid="tool-progress">
          <div className="flex items-center justify-between gap-2 text-xs text-muted">
            <span className="truncate">{p.stage}{p.total ? ` · ${p.step}/${p.total}` : ""}</span>
            {p.elapsed_s != null && <span className="shrink-0 font-mono text-faint">{fmtElapsed(p.elapsed_s)}</span>}
          </div>
          {p.total ? (
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label={p.stage}
              aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.step}>
              <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${(100 * (p.step ?? 0)) / p.total}%` }} />
            </div>
          ) : null}
        </div>
      )}

      {videos.length > 0 && (
        <div className="space-y-2 px-3 pb-3">
          {videos.map((f) => cleared.has(f.id!) ? <ClearedNote key={f.id} what="video" /> : (
            <video key={f.id} src={fileUrl(f.id!)} controls playsInline loop preload="metadata" data-testid="generated-video"
              onError={() => markCleared(f.id!)} aria-label={f.filename} className="max-h-[28rem] w-full rounded-md border border-line bg-black" />
          ))}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {tool.data?.width != null && (
              <span className="min-w-0 flex-1 text-xs text-faint">
                {String(tool.data.width)}×{String(tool.data.height)} · {String(tool.data.seconds)}s{tool.data.audio ? " · with sound" : ""} · seed {String(tool.data.seed)}
                {tool.data.elapsed_s != null && ` · rendered in ${fmtElapsed(Number(tool.data.elapsed_s))}`}
              </span>
            )}
            {videos.filter((f) => !cleared.has(f.id!)).map((f) => (
              <a key={f.id} href={fileUrl(f.id!, { download: true })} download={f.filename} data-testid="download-video"
                className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium hover:bg-surface-2">
                <Download size={13} /> Download{videos.length > 1 ? ` ${f.filename}` : ""}
              </a>
            ))}
          </div>
        </div>
      )}

      {images.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3 pb-3">
          {images.map((f) => cleared.has(f.id!) ? <ClearedNote key={f.id} what="image" /> : (
            <a key={f.id} href={fileUrl(f.id!)} target="_blank" rel="noreferrer">
              <img src={fileUrl(f.id!)} alt={f.filename} onError={() => markCleared(f.id!)} className="max-h-72 rounded-md border border-line bg-white" />
            </a>
          ))}
        </div>
      )}

      {open && (
        <div className="space-y-2 border-t border-line px-3 py-2">
          {code ? (
            <pre className="max-h-80 overflow-auto rounded-md bg-code p-2 font-mono text-xs leading-relaxed">{code}</pre>
          ) : (
            <pre className="max-h-48 overflow-auto rounded-md bg-code p-2 font-mono text-xs">{JSON.stringify(tool.arguments, null, 2)}</pre>
          )}
          {tool.preview && (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-code p-2 font-mono text-xs text-muted">{tool.preview}</pre>
          )}
          {tool.decision && tool.decision !== "allow" && <div className="text-xs text-faint">Policy: {tool.decision}</div>}
        </div>
      )}
    </div>
  );
}

function fmtElapsed(s: number) {
  const m = Math.floor(s / 60);
  return m ? `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${Math.round(s)}s`;
}

export function ThinkingBlock({ text, done }: { text: string; done: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <Brain size={14} className={clsx(!done && "pulse")} />
        <span>{done ? "Thought process" : "Thinking…"}</span>
        <ChevronRight size={14} className={clsx("transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-1.5 max-h-96 overflow-auto whitespace-pre-wrap border-l-2 border-line pl-3 text-sm text-muted">{text}</div>
      )}
    </div>
  );
}
