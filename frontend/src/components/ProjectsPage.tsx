import clsx from "clsx";
import { Check, FileText, FolderClosed, Loader2, MessageSquarePlus, PanelLeftOpen, Plus, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { del, get, patch, post, upload } from "../api/client";
import type { Attachment, Conversation, Project, SearchResult } from "../api/types";
import { useChat } from "../store/chat";
import { useUi } from "../store/ui";
import { fmtBytes } from "./AttachmentView";
import { ConfirmDialog } from "./Modal";

interface ProjectDetail extends Project {
  files: Attachment[];
  conversations: Conversation[];
}

function Header({ title }: { title: string }) {
  const { sidebar, setSidebar } = useUi();
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
      {!sidebar && <button type="button" onClick={() => setSidebar(true)} aria-label="Show sidebar" className="rounded-md p-1.5 text-muted hover:bg-surface-2"><PanelLeftOpen size={17} /></button>}
      <Link to="/projects" className="text-sm text-muted hover:text-fg">Projects</Link>
      {title && <><span className="text-faint">/</span><h1 className="truncate text-sm font-medium">{title}</h1></>}
    </header>
  );
}

export function ProjectsPage() {
  const { id } = useParams();
  return id ? <ProjectDetailPage id={id} /> : <ProjectList />;
}

function ProjectList() {
  const { projects, loadProjects } = useChat();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  useEffect(() => { loadProjects(); }, [loadProjects]);
  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <Header title="" />
      <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
        <div>
          <h1 className="text-xl font-semibold">Projects</h1>
          <p className="mt-1 text-sm text-muted">Group chats around a topic, with shared instructions and knowledge files that every chat in the project can use.</p>
        </div>
        <form className="flex gap-2" onSubmit={async (e) => {
          e.preventDefault();
          if (!name.trim()) return;
          const p = await post<Project>("/projects", { name: name.trim() });
          setName("");
          loadProjects();
          navigate(`/projects/${p.id}`);
        }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New project name" aria-label="Project name"
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent" />
          <button type="submit" disabled={!name.trim()} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent disabled:opacity-40"><Plus size={15} /> Create</button>
        </form>
        <ul className="grid gap-3 sm:grid-cols-2">
          {projects.map((p) => (
            <li key={p.id}>
              <Link to={`/projects/${p.id}`} className="flex h-full items-start gap-3 rounded-xl border border-line bg-surface p-4 hover:border-accent/50">
                <FolderClosed size={18} className="mt-0.5 shrink-0 text-accent" />
                <span className="min-w-0">
                  <span className="block font-medium">{p.name}</span>
                  <span className="line-clamp-2 text-sm text-muted">{p.instructions || "No instructions yet"}</span>
                </span>
              </Link>
            </li>
          ))}
          {!projects.length && <li className="text-sm text-faint">No projects yet.</li>}
        </ul>
      </div>
    </main>
  );
}

interface Uploading { key: string; name: string; progress: number; state: "uploading" | "indexing" | "done" | "error"; error?: string }

function ProjectDetailPage({ id }: { id: string }) {
  const navigate = useNavigate();
  const { newChat, showToast, loadProjects } = useChat();
  const [p, setP] = useState<ProjectDetail | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState(false);
  const [uploads, setUploads] = useState<Uploading[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = async () => {
    const d = await get<ProjectDetail>(`/projects/${id}`);
    setP(d);
    setName(d.name);
    setInstructions(d.instructions);
  };
  useEffect(() => { load().catch(() => navigate("/projects")); }, [id]);
  useEffect(() => {
    if (q.trim().length < 2) return setResults(null);
    const t = setTimeout(() => get<SearchResult>(`/search?q=${encodeURIComponent(q)}&project_id=${id}`).then(setResults), 250);
    return () => clearTimeout(t);
  }, [q, id]);

  const addFiles = async (files: File[]) => {
    for (const f of files) {
      const key = `${f.name}-${Date.now()}-${Math.random()}`;
      const set = (u: Partial<Uploading>) => setUploads((us) => us.map((x) => (x.key === key ? { ...x, ...u } : x)));
      setUploads((us) => [...us, { key, name: f.name, progress: 0, state: "uploading" }]);
      try {
        const att = await upload(f, f.name, { onProgress: (x) => set({ progress: x }) });
        set({ state: "indexing" });
        await post(`/projects/${id}/files`, { attachment_id: att.id });
        set({ state: "done" });
        setTimeout(() => setUploads((us) => us.filter((x) => x.key !== key)), 1500);
        load();
      } catch (e) {
        set({ state: "error", error: String((e as Error).message).slice(0, 120) });
      }
    }
  };

  const startChat = async () => {
    newChat();
    const conv = await post<Conversation>("/conversations", { project_id: id });
    navigate(`/c/${conv.id}`);
  };

  if (!p) return <main className="flex-1"><Header title="" /></main>;
  const dirty = name !== p.name || instructions !== p.instructions;

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-y-auto"
      onDragEnter={(e) => { if (e.dataTransfer.types.includes("Files")) setDragging(true); }}
      onDragOver={(e) => e.dataTransfer.types.includes("Files") && e.preventDefault()}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles([...e.dataTransfer.files]); }}>
      <Header title={p.name} />
      <div className="mx-auto grid w-full max-w-5xl gap-6 px-4 py-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Project name"
              className="min-w-0 flex-1 bg-transparent text-xl font-semibold outline-none focus:underline" />
            <button type="button" onClick={startChat} className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-on-accent"><MessageSquarePlus size={15} /> New chat</button>
          </div>
          <section className="space-y-2">
            <h2 className="text-sm font-medium">Instructions</h2>
            <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={5} aria-label="Project instructions"
              placeholder="How should the assistant behave in this project? e.g. “You're helping me plan the Falcon launch. Answer briefly and cite the brief.”"
              className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent" />
            <button type="button" disabled={!dirty} onClick={async () => {
              await patch(`/projects/${id}`, { name: name.trim() || p.name, instructions });
              await load();
              loadProjects();
              setSaved(true);
              setTimeout(() => setSaved(false), 1500);
            }} className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40">{saved ? <><Check size={14} /> Saved</> : "Save"}</button>
          </section>
          <section className="space-y-2">
            <h2 className="text-sm font-medium">Chats in this project</h2>
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute top-2.5 left-2.5 text-faint" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search this project's chats" aria-label="Search project chats"
                className="w-full rounded-lg border border-line bg-surface py-1.5 pr-3 pl-8 text-sm outline-none focus:border-accent" />
            </div>
            <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
              {(results ? [...results.titles, ...results.semantic.map((s) => s.conversation).filter((c) => !results.titles.some((t) => t.id === c.id))] : p.conversations).map((c) => (
                <li key={c.id}><Link to={`/c/${c.id}`} className="block truncate px-3 py-2 text-sm hover:bg-surface-2">{c.title || "Untitled chat"}</Link></li>
              ))}
              {!p.conversations.length && !results && <li className="px-3 py-3 text-sm text-faint">No chats yet.</li>}
            </ul>
          </section>
          <button type="button" onClick={() => setConfirmDelete(true)} className="flex items-center gap-1.5 text-sm text-danger hover:underline"><Trash2 size={14} /> Delete project</button>
        </div>

        <aside className="space-y-2">
          <h2 className="text-sm font-medium">Knowledge</h2>
          <p className="text-xs text-muted">Files every chat in this project can use. Small sets are included in full; larger ones are searched per question.</p>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => { addFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
          <button type="button" onClick={() => fileInput.current?.click()}
            className={clsx("flex w-full flex-col items-center gap-1 rounded-xl border-2 border-dashed px-3 py-5 text-sm",
              dragging ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:border-accent/50")}>
            <Upload size={18} /> Add files <span className="text-xs text-faint">or drop them here · PDF, DOCX, PPTX, XLSX, CSV, Markdown, code</span>
          </button>
          <ul className="space-y-1.5" data-testid="project-files">
            {uploads.map((u) => (
              <li key={u.key} className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
                {u.state === "error" ? <span className="text-danger">!</span> : u.state === "done" ? <Check size={14} className="text-accent" /> : <Loader2 size={14} className="animate-spin text-muted" />}
                <span className="min-w-0 flex-1 truncate">{u.name}</span>
                <span className="text-xs text-faint">{u.state === "uploading" ? `${Math.round(u.progress * 100)}%` : u.state === "indexing" ? "indexing…" : u.error ?? ""}</span>
              </li>
            ))}
            {p.files.map((f) => (
              <li key={f.id} className="group flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
                <FileText size={15} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate" title={f.filename}>{f.filename}</span>
                <span className="text-xs text-faint">{fmtBytes(f.size)}</span>
                <button type="button" aria-label={`Remove ${f.filename}`} onClick={async () => { await del(`/projects/${id}/files/${f.id}`); load(); }}
                  className="rounded p-1 text-muted opacity-0 hover:text-danger group-hover:opacity-100 focus:opacity-100 max-md:opacity-100"><Trash2 size={13} /></button>
              </li>
            ))}
            {!p.files.length && !uploads.length && <li className="text-xs text-faint">No files yet.</li>}
          </ul>
        </aside>
      </div>
      {confirmDelete && <ConfirmDialog title="Delete project?" danger confirmLabel="Delete"
        body={<>“{p.name}” and its knowledge index will be deleted. Its chats are kept and move out of the project.</>}
        onConfirm={async () => { await del(`/projects/${id}`); loadProjects(); showToast("Project deleted"); navigate("/projects"); }}
        onClose={() => setConfirmDelete(false)} />}
    </main>
  );
}
