import clsx from "clsx";
import {
  Cpu, EyeOff, FolderClosed, Keyboard, Monitor, Moon, MoreHorizontal, PanelLeftClose, Pencil, Search, Settings, SquarePen,
  Star, StarOff, Sun, Trash2, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { get } from "../api/client";
import type { Conversation, SearchResult } from "../api/types";
import { recencyGroup } from "../lib/tree";
import { useChat } from "../store/chat";
import { useUi, type Theme } from "../store/ui";
import { ConfirmDialog } from "./Modal";
import { SidebarOptions } from "./SidebarOptions";
import { SidebarWebAccess } from "./WebAccessSwitch";

function ConversationRow({ c, active }: { c: Conversation; active: boolean }) {
  const { rename, star, remove } = useChat();
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(c.title ?? "");
  const [confirm, setConfirm] = useState(false);
  const closeSidebarOnMobile = useUi((s) => s.closeOnMobile);
  return (
    <li className="group relative">
      {editing ? (
        <input autoFocus value={title} aria-label="Chat title" onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { setEditing(false); if (title.trim() && title !== c.title) rename(c.id, title.trim()); }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setTitle(c.title ?? ""); setEditing(false); } }}
          className="w-full rounded-lg border border-accent bg-surface px-2.5 py-1.5 text-sm outline-none" />
      ) : (
        <Link to={`/c/${c.id}`} onClick={closeSidebarOnMobile} onDoubleClick={() => setEditing(true)}
          aria-current={active ? "page" : undefined}
          className={clsx("flex items-center gap-2 rounded-lg px-2.5 py-1.5 pr-8 text-sm", active ? "bg-surface-3 text-fg" : "text-muted hover:bg-surface-2 hover:text-fg")}>
          <span className="truncate">{c.title || "New chat"}</span>
        </Link>
      )}
      {!editing && (
        <button type="button" onClick={() => setMenu((v) => !v)} aria-label={`Options for ${c.title || "chat"}`}
          className="absolute top-1 right-1 rounded-md p-1 text-muted opacity-0 hover:bg-surface-3 group-hover:opacity-100 focus:opacity-100 max-md:opacity-100">
          <MoreHorizontal size={15} />
        </button>
      )}
      {menu && (
        <div role="menu" onMouseLeave={() => setMenu(false)} className="absolute top-8 right-1 z-30 w-40 rounded-lg border border-line bg-surface p-1 text-sm shadow-card">
          <button role="menuitem" type="button" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2" onClick={() => { setMenu(false); star(c.id, !c.starred); }}>
            {c.starred ? <StarOff size={14} /> : <Star size={14} />} {c.starred ? "Unstar" : "Star"}
          </button>
          <button role="menuitem" type="button" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2" onClick={() => { setMenu(false); setEditing(true); }}>
            <Pencil size={14} /> Rename
          </button>
          <button role="menuitem" type="button" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-danger hover:bg-danger-soft" onClick={() => { setMenu(false); setConfirm(true); }}>
            <Trash2 size={14} /> Delete
          </button>
        </div>
      )}
      {confirm && <ConfirmDialog title="Delete chat?" danger confirmLabel="Delete"
        body={<>“{c.title || "New chat"}” and its attachments' search index will be deleted. This can't be undone.</>}
        onConfirm={() => remove(c.id)} onClose={() => setConfirm(false)} />}
    </li>
  );
}

function SearchResults({ q, onPick }: { q: string; onPick: () => void }) {
  const [res, setRes] = useState<SearchResult | null>(null);
  useEffect(() => {
    const t = setTimeout(() => get<SearchResult>(`/search?q=${encodeURIComponent(q)}`).then(setRes).catch(() => setRes(null)), 250);
    return () => clearTimeout(t);
  }, [q]);
  if (!res) return <p className="px-3 py-2 text-sm text-faint">Searching…</p>;
  const seen = new Set(res.titles.map((c) => c.id));
  const semantic = res.semantic.filter((s) => !seen.has(s.conversation.id));
  if (!res.titles.length && !semantic.length) return <p className="px-3 py-2 text-sm text-faint">No chats match “{q}”.</p>;
  return (
    <ul className="space-y-0.5">
      {res.titles.map((c) => (
        <li key={c.id}><Link to={`/c/${c.id}`} onClick={onPick} className="block truncate rounded-lg px-2.5 py-1.5 text-sm hover:bg-surface-2">{c.title}</Link></li>
      ))}
      {semantic.map((s) => (
        <li key={s.conversation.id}>
          <Link to={`/c/${s.conversation.id}`} onClick={onPick} className="block rounded-lg px-2.5 py-1.5 hover:bg-surface-2">
            <span className="block truncate text-sm">{s.conversation.title || "Untitled"}</span>
            <span className="block truncate text-xs text-faint">{s.snippets[0]?.text}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const THEMES: { id: Theme; icon: typeof Sun; label: string }[] = [
  { id: "light", icon: Sun, label: "Light" }, { id: "dark", icon: Moon, label: "Dark" }, { id: "system", icon: Monitor, label: "System" },
];

export function Sidebar() {
  const { conversations, loadConversations, newChat, incognito, setIncognito, models } = useChat();
  const { theme, setTheme, setSidebar, setShortcuts, setModelsPanel, closeOnMobile } = useUi();
  const { id } = useParams();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  const groups = useMemo(() => {
    const starred = conversations.filter((c) => c.starred);
    const rest = conversations.filter((c) => !c.starred);
    const out: [string, Conversation[]][] = [];
    if (starred.length) out.push(["Starred", starred]);
    for (const c of rest) {
      const g = recencyGroup(c.updated_at);
      const bucket = out.find(([name]) => name === g);
      if (bucket) bucket[1].push(c);
      else out.push([g, [c]]);
    }
    return out;
  }, [conversations]);

  const loadedLlms = models?.llms.filter((l) => l.loaded) ?? [];

  return (
    <nav aria-label="Chats" className="flex h-full w-72 flex-col border-r border-line bg-surface-2/60">
      <div className="flex items-center gap-1 px-3 pt-3 pb-2">
        <span className="flex-1 pl-1" role="img" aria-label="GoRunRun Local AI">
          <img src="/brand/lockup-horizontal.svg" alt="" className="logo-light h-10 w-auto" />
          <img src="/brand/lockup-horizontal-on-dark.svg" alt="" className="logo-dark h-10 w-auto" />
        </span>
        <button type="button" onClick={() => setSidebar(false)} aria-label="Hide sidebar" className="rounded-md p-1.5 text-muted hover:bg-surface-3"><PanelLeftClose size={17} /></button>
      </div>
      <SidebarOptions onNavigate={closeOnMobile} />
      <div className="space-y-1 px-3">
        <SidebarWebAccess />
        <button type="button" onClick={() => { newChat(); navigate("/"); closeOnMobile(); }}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium hover:bg-surface-3">
          <SquarePen size={16} className="text-accent" /> New chat <kbd className="ml-auto text-[11px] font-normal text-faint">⌘K</kbd>
        </button>
        <button type="button" onClick={() => { setIncognito(!incognito); navigate("/"); closeOnMobile(); }} aria-pressed={incognito}
          className={clsx("flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm", incognito ? "bg-surface-3 font-medium" : "text-muted hover:bg-surface-3 hover:text-fg")}>
          <EyeOff size={16} /> {incognito ? "Incognito on" : "Incognito chat"}
        </button>
        <div className="relative">
          <Search size={14} className="pointer-events-none absolute top-2.5 left-2.5 text-faint" />
          <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats" aria-label="Search chats"
            className="w-full rounded-lg border border-line bg-surface py-1.5 pr-7 pl-8 text-sm outline-none focus:border-accent" />
          {q && <button type="button" onClick={() => setQ("")} aria-label="Clear search" className="absolute top-1.5 right-1.5 rounded p-0.5 text-faint hover:text-fg"><X size={14} /></button>}
        </div>
      </div>

      <div className="mt-2 min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {q.trim().length > 1 ? <SearchResults q={q.trim()} onPick={closeOnMobile} /> : groups.length === 0 ? (
          <p className="px-2.5 py-6 text-sm text-faint">No chats yet. Start one below.</p>
        ) : groups.map(([name, list]) => (
          <section key={name} className="mt-3">
            <h3 className="mb-1 flex items-center gap-1 px-2.5 text-[11px] font-medium uppercase tracking-wider text-faint">
              {name === "Starred" && <Star size={11} />}{name}
            </h3>
            <ul className="space-y-0.5">{list.map((c) => <ConversationRow key={c.id} c={c} active={c.id === id} />)}</ul>
          </section>
        ))}
      </div>

      <div className="space-y-1 border-t border-line px-3 py-2">
        <Link to="/projects" onClick={closeOnMobile} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-fg">
          <FolderClosed size={15} /> Projects
        </Link>
        <Link to="/settings" onClick={closeOnMobile} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-fg">
          <Settings size={15} /> Settings
        </Link>
        <button type="button" onClick={() => setModelsPanel(true)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-fg">
          <Cpu size={15} />
          <span className="truncate">{loadedLlms.length ? loadedLlms.map((l) => l.display_name).join(" + ") : "Models"}</span>
          {models && <span className="ml-auto text-xs text-faint tabular-nums">{models.models_total_gb.toFixed(0)} GB</span>}
        </button>
        <div className="flex items-center gap-1 pt-1">
          <div role="radiogroup" aria-label="Theme" className="flex rounded-lg bg-surface-3 p-0.5">
            {THEMES.map((t) => (
              <button key={t.id} type="button" role="radio" aria-checked={theme === t.id} aria-label={t.label} title={t.label} onClick={() => setTheme(t.id)}
                className={clsx("rounded-md p-1.5", theme === t.id ? "bg-surface text-fg shadow-sm" : "text-muted")}>
                <t.icon size={14} />
              </button>
            ))}
          </div>
          <span className="flex-1" />
          <button type="button" onClick={() => setShortcuts(true)} aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)" className="rounded-md p-1.5 text-muted hover:bg-surface-3"><Keyboard size={16} /></button>
        </div>
      </div>
    </nav>
  );
}
