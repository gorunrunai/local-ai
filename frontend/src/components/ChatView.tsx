import clsx from "clsx";
import { ChevronDown, Download, EyeOff, FileJson, FileText, FolderClosed, PanelLeftOpen, Printer, Star, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { apiUrl, getToken } from "../api/client";
import { useChat } from "../store/chat";
import { useDraft } from "../store/draft";
import { useUi } from "../store/ui";
import { ArtifactPanel } from "./ArtifactPanel";
import { Composer } from "./Composer";
import { MessageList } from "./Messages";
import { OfflineBadge } from "./OfflineBadge";
import { SetupSummary } from "./SetupSummary";

function ModelPicker() {
  const { models, current, setConversationFields } = useChat();
  const [open, setOpen] = useState(false);
  if (!models) return null;
  const active = current?.conversation.model ?? models.defaults.llm;
  const activeInfo = models.llms.find((l) => l.id === active);
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-haspopup="listbox" aria-expanded={open}
        className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm hover:bg-surface-2">
        <span className="font-medium">{activeInfo?.display_name ?? active}</span><ChevronDown size={14} className="text-muted" />
      </button>
      {open && (
        <ul role="listbox" onMouseLeave={() => setOpen(false)} className="absolute top-full right-0 z-30 mt-1 w-72 rounded-xl border border-line bg-surface p-1 shadow-card">
          {models.llms.filter((l) => l.capabilities.tools && l.installed !== false).map((l) => (
            <li key={l.id} role="option" aria-selected={l.id === active}>
              <button type="button" onClick={() => { setOpen(false); setConversationFields({ model: l.id }); }}
                className={clsx("w-full rounded-lg px-3 py-2 text-left hover:bg-surface-2", l.id === active && "bg-surface-2")}>
                <span className="flex items-center justify-between text-sm font-medium">{l.display_name}
                  <span className={clsx("text-xs font-normal", l.loaded ? "text-accent" : "text-faint")}>{l.loaded ? "loaded" : `${l.est_memory_gb} GB`}</span>
                </span>
                <span className="block text-xs text-muted">
                  {[l.capabilities.image && "images", l.capabilities.video && "video", l.capabilities.audio && "audio", "tools"].filter(Boolean).join(" · ")}
                  {l.speed?.decode_tok_s ? ` · ${Math.round(l.speed.decode_tok_s)} tok/s` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProjectChip({ id }: { id: string }) {
  const projects = useChat((s) => s.projects);
  const loadProjects = useChat((s) => s.loadProjects);
  useEffect(() => { if (!projects.some((p) => p.id === id)) loadProjects(); }, [id, projects, loadProjects]);
  const p = projects.find((x) => x.id === id);
  return (
    <Link to={`/projects/${id}`} className="flex shrink-0 items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent hover:underline">
      <FolderClosed size={12} /> {p?.name ?? "Project"}
    </Link>
  );
}

function ExportMenu({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const download = async (format: "md" | "json") => {
    setOpen(false);
    const t = getToken();
    const res = await fetch(apiUrl(`/conversations/${id}/export?format=${format}`), { headers: t ? { Authorization: `Bearer ${t}` } : {} });
    const blob = format === "json" ? new Blob([JSON.stringify(await res.json(), null, 2)], { type: "application/json" }) : await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${useChat.getState().current?.conversation.title ?? "conversation"}.${format}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-label="Export chat" title="Export" className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-fg"><Download size={16} /></button>
      {open && (
        <div role="menu" onMouseLeave={() => setOpen(false)} className="absolute top-full right-0 z-30 mt-1 w-44 rounded-lg border border-line bg-surface p-1 text-sm shadow-card">
          <button role="menuitem" type="button" onClick={() => download("md")} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2"><FileText size={14} /> Markdown</button>
          <button role="menuitem" type="button" onClick={() => download("json")} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2"><FileJson size={14} /> JSON</button>
          <button role="menuitem" type="button" onClick={() => { setOpen(false); setTimeout(() => window.print(), 50); }} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2"><Printer size={14} /> PDF (print)</button>
        </div>
      )}
    </div>
  );
}

const SUGGESTIONS = [
  "Explain how a transformer's attention works, with a small diagram",
  "Write Python to plot my CSV and summarize it",
  "What's in this screenshot? (paste one with ⌘V)",
  "Help me draft a polite reply to this email",
];

function EmptyState() {
  const incognito = useChat((s) => s.incognito);
  const setText = useDraft((s) => s.setText);
  const focus = useDraft((s) => s.focusComposer);
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-6 py-8 text-center"><div className="my-auto flex w-full flex-col items-center">
      {incognito ? (
        <>
          <EyeOff size={28} className="mb-3 text-muted" />
          <h1 className="text-2xl font-semibold tracking-tight">Incognito chat</h1>
          <p className="mt-2 max-w-md text-sm text-muted">Nothing here is saved, and your saved memories aren't read or changed. The chat disappears when you leave it.</p>
        </>
      ) : (
        <>
          <img src="/brand/mark.svg" alt="" className="logo-light mb-5 h-20 w-auto" />
          <img src="/brand/mark-on-dark.svg" alt="" className="logo-dark mb-5 h-20 w-auto" />
          <h1 className="text-2xl font-semibold tracking-tight">What can I help with?</h1>
          <OfflineBadge />
          <div className="mt-6 grid w-full max-w-xl gap-2 sm:grid-cols-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" onClick={() => { setText(s); focus(); }}
                className="rounded-xl border border-line bg-surface px-3 py-2.5 text-left text-sm text-muted hover:border-accent/50 hover:text-fg">{s}</button>
            ))}
          </div>
          <div className="mt-6 w-full max-w-xl"><SetupSummary /></div>
        </>
      )}
    </div></div>
  );
}

export function ChatView() {
  return (
    <div className="flex min-w-0 flex-1">
      <ChatColumn />
      <ArtifactPanel />
    </div>
  );
}

function ChatColumn() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { current, openConversation, newChat, star, incognito, consumeNavigation, navigateTo, sending, live } = useChat();
  const { sidebar, setSidebar, closeArtifact } = useUi();
  const addFiles = useDraft((s) => s.addFiles);
  const [dragging, setDragging] = useState(0);

  useEffect(() => {
    closeArtifact();
    if (id) openConversation(id);
    else if (current?.conversation.id && current.messages.length && !current.conversation.incognito) newChat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    const to = consumeNavigation();
    if (to) navigate(to, { replace: true });
  }, [navigateTo, consumeNavigation, navigate]);

  const empty = !current?.messages.length && !sending && !live;
  const conv = current?.conversation;

  return (
    <main className="relative flex min-w-0 flex-1 flex-col"
      onDragEnter={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging((n) => n + 1); } }}
      onDragOver={(e) => e.dataTransfer.types.includes("Files") && e.preventDefault()}
      onDragLeave={() => setDragging((n) => Math.max(0, n - 1))}
      onDrop={(e) => { e.preventDefault(); setDragging(0); addFiles([...e.dataTransfer.files]); }}>
      <header className="no-print flex h-12 shrink-0 items-center gap-2 border-b border-line px-3" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        {!sidebar && <button type="button" onClick={() => setSidebar(true)} aria-label="Show sidebar" className="rounded-md p-1.5 text-muted hover:bg-surface-2"><PanelLeftOpen size={17} /></button>}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {incognito && <span className="flex shrink-0 items-center gap-1 rounded-full border border-dashed border-muted px-2 py-0.5 text-xs text-muted"><EyeOff size={12} /> Incognito</span>}
          {conv?.project_id && <ProjectChip id={conv.project_id} />}
          <h1 className="truncate text-sm font-medium">{conv?.title ?? (empty ? "" : "New chat")}</h1>
          {conv && !conv.incognito && conv.title && (
            <button type="button" onClick={() => star(conv.id, !conv.starred)} aria-label={conv.starred ? "Unstar chat" : "Star chat"} aria-pressed={!!conv.starred}
              className={clsx("rounded-md p-1", conv.starred ? "text-warn" : "text-faint hover:text-fg")}>
              <Star size={15} className={conv.starred ? "fill-current" : ""} />
            </button>
          )}
        </div>
        <ModelPicker />
        {conv && current?.messages.length ? <ExportMenu id={conv.id} /> : null}
      </header>

      {empty ? <EmptyState /> : <MessageList />}
      <Composer />

      {dragging > 0 && (
        <div className="pointer-events-none absolute inset-2 z-40 grid place-items-center rounded-2xl border-2 border-dashed border-accent bg-accent-soft/80">
          <div className="flex flex-col items-center gap-2 text-accent"><Upload size={28} /><span className="font-medium">Drop files to attach</span>
            <span className="text-xs">Images, screenshots, audio, video, PDFs, documents, code</span></div>
        </div>
      )}
    </main>
  );
}
