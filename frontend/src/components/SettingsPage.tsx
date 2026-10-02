import clsx from "clsx";
import { Check, Copy, Download, Loader2, PanelLeftOpen, Pencil, Play, Plus, RefreshCw, Share, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useParams } from "react-router";
import QRCode from "qrcode";
import { apiUrl, del, get, getToken, patch, post, signInLink } from "../api/client";
import type { ModelStatus } from "../api/types";
import { useChat } from "../store/chat";
import { useUi, type Theme } from "../store/ui";
import { Markdown, useCopy } from "./Markdown";
import { SetupSummary } from "./SetupSummary";
import { Slider } from "./Slider";
import { VideoLength } from "./VideoLength";
import { ConfirmDialog } from "./Modal";
import { useWebAccess } from "./WebAccessSwitch";
import { refreshCapabilities } from "../lib/capabilities";
import { shareLink } from "../lib/share";

interface Prefs {
  custom_instructions: string;
  memory_enabled: boolean;
  style: string;
  temperature: number;
  max_tokens: number;
  context_tokens: number | null;
  video_frame_budget: number;
  video_max_seconds: Record<string, number>;
  locale: string;
  timezone: string | null;
  voice: { stt: string; tts_voice: string; speed: number; vad_sensitivity: number; push_to_talk_key: string };
}

const SECTIONS = [
  ["general", "General"], ["styles", "Styles"], ["memory", "Memory"], ["tools", "Tools & MCP"],
  ["models", "Models"], ["voice", "Voice"], ["remote", "Phone access"], ["setup", "Your setup"], ["storage", "Clear clutter"], ["updates", "Updates"],
] as const;

const input = "w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent";
const btn = "inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40";
const primary = "inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-on-accent hover:bg-accent-strong disabled:opacity-40";

function Section({ title, desc, children }: { title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface p-5">
      <div>
        <h2 className="font-semibold">{title}</h2>
        {desc && <p className="mt-0.5 text-sm text-muted">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={clsx("relative h-6 w-11 shrink-0 rounded-full transition-colors", checked ? "bg-accent" : "bg-surface-3")}>
      <span className={clsx("absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform", checked && "translate-x-5")} />
    </button>
  );
}


function useSaved() {
  const [saved, setSaved] = useState(false);
  return [saved, () => { setSaved(true); setTimeout(() => setSaved(false), 1500); }] as const;
}

// --- sections -----------------------------------------------------------------------------
function General({ prefs, save }: { prefs: Prefs; save: (p: Partial<Prefs>) => Promise<void> }) {
  const { theme, setTheme } = useUi();
  const [ci, setCi] = useState(prefs.custom_instructions);
  const [saved, flash] = useSaved();
  return (
    <>
      <Section title="Appearance">
        <div className="flex gap-2">
          {(["light", "dark", "system"] as Theme[]).map((t) => (
            <button key={t} type="button" onClick={() => setTheme(t)} aria-pressed={theme === t}
              className={clsx(btn, "capitalize", theme === t && "border-accent bg-accent-soft text-accent")}>{t}</button>
          ))}
        </div>
      </Section>
      <Section title="Custom instructions" desc="Applied to every chat. Tell the assistant about you and how you like answers.">
        <textarea value={ci} onChange={(e) => setCi(e.target.value)} rows={6} aria-label="Custom instructions" className={input}
          placeholder="e.g. I'm a data engineer. Prefer Python examples. Keep answers short unless I ask for detail." />
        <button type="button" className={primary} disabled={ci === prefs.custom_instructions}
          onClick={async () => { await save({ custom_instructions: ci }); flash(); }}>{saved ? <><Check size={14} /> Saved</> : "Save"}</button>
      </Section>
      <Section title="Region">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Locale<input className={input} defaultValue={prefs.locale} onBlur={(e) => save({ locale: e.target.value })} /></label>
          <label className="text-sm">Time zone<input className={input} defaultValue={prefs.timezone ?? ""} placeholder="System default"
            onBlur={(e) => save({ timezone: e.target.value || null })} /></label>
        </div>
      </Section>
    </>
  );
}

function Styles({ prefs, save }: { prefs: Prefs; save: (p: Partial<Prefs>) => Promise<void> }) {
  const [styles, setStyles] = useState<{ presets: Record<string, string>; custom: Record<string, string> } | null>(null);
  const [edit, setEdit] = useState<{ name: string; instructions: string; original?: string } | null>(null);
  const [sample, setSample] = useState({ name: "", text: "" });
  const [busy, setBusy] = useState(false);
  const load = () => get<typeof styles>("/styles").then(setStyles);
  useEffect(() => { load(); }, []);
  if (!styles) return <Loader2 className="animate-spin" />;
  const all = ["default", ...Object.keys(styles.presets), ...Object.keys(styles.custom)];
  return (
    <>
      <Section title="Default style" desc="New chats use this style. Change it for one chat with /style.">
        <div className="flex flex-wrap gap-2">
          {all.map((s) => (
            <button key={s} type="button" onClick={() => save({ style: s })} aria-pressed={prefs.style === s}
              className={clsx(btn, "capitalize", prefs.style === s && "border-accent bg-accent-soft text-accent")}>{s}</button>
          ))}
        </div>
      </Section>
      <Section title="Presets">
        <dl className="space-y-2 text-sm">
          {Object.entries(styles.presets).map(([k, v]) => <div key={k}><dt className="font-medium capitalize">{k}</dt><dd className="text-muted">{v}</dd></div>)}
        </dl>
      </Section>
      <Section title="Your styles">
        <ul className="space-y-2">
          {Object.entries(styles.custom).map(([k, v]) => (
            <li key={k} className="flex items-start gap-2 rounded-lg border border-line p-3 text-sm">
              <div className="min-w-0 flex-1"><div className="font-medium">{k}</div><div className="text-muted">{v}</div></div>
              <button type="button" aria-label={`Edit ${k}`} className="rounded p-1 text-muted hover:bg-surface-2" onClick={() => setEdit({ name: k, instructions: v, original: k })}><Pencil size={14} /></button>
              <button type="button" aria-label={`Delete ${k}`} className="rounded p-1 text-muted hover:bg-danger-soft hover:text-danger" onClick={async () => { await del(`/styles/${encodeURIComponent(k)}`); load(); }}><Trash2 size={14} /></button>
            </li>
          ))}
          {!Object.keys(styles.custom).length && <li className="text-sm text-faint">No custom styles yet.</li>}
        </ul>
        {edit ? (
          <div className="space-y-2 rounded-lg border border-line p-3">
            <input className={input} placeholder="Style name" aria-label="Style name" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            <textarea className={input} rows={4} aria-label="Style instructions" placeholder="How should the assistant write?" value={edit.instructions} onChange={(e) => setEdit({ ...edit, instructions: e.target.value })} />
            <div className="flex gap-2">
              <button type="button" className={primary} disabled={!edit.name.trim() || !edit.instructions.trim()} onClick={async () => {
                if (edit.original && edit.original !== edit.name) await del(`/styles/${encodeURIComponent(edit.original)}`);
                await api("PUT", `/styles/${encodeURIComponent(edit.name.trim())}`, { instructions: edit.instructions });
                setEdit(null);
                load();
              }}>Save style</button>
              <button type="button" className={btn} onClick={() => setEdit(null)}>Cancel</button>
            </div>
          </div>
        ) : <button type="button" className={btn} onClick={() => setEdit({ name: "", instructions: "" })}><Plus size={14} /> New style</button>}
      </Section>
      <Section title="Create a style from a writing sample" desc="Paste something you wrote; the assistant describes its style so replies can match it.">
        <input className={input} placeholder="Style name" aria-label="Sample style name" value={sample.name} onChange={(e) => setSample({ ...sample, name: e.target.value })} />
        <textarea className={input} rows={6} aria-label="Writing sample" placeholder="Paste at least a paragraph (50+ characters)" value={sample.text} onChange={(e) => setSample({ ...sample, text: e.target.value })} />
        <button type="button" className={primary} disabled={busy || !sample.name.trim() || sample.text.length < 50} onClick={async () => {
          setBusy(true);
          try {
            await post("/styles/from-sample", { name: sample.name.trim(), sample: sample.text });
            setSample({ name: "", text: "" });
            load();
          } finally {
            setBusy(false);
          }
        }}>{busy ? <><Loader2 size={14} className="animate-spin" /> Analyzing…</> : "Create style"}</button>
      </Section>
    </>
  );
}

async function api(method: string, path: string, body?: unknown) {
  const t = getToken();
  const res = await fetch(apiUrl(path), { method, headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? res.statusText);
  return res.json();
}

interface Memory { id: string; text: string; updated_at: number }

function MemorySection({ prefs, save }: { prefs: Prefs; save: (p: Partial<Prefs>) => Promise<void> }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState("");
  const load = () => get<Memory[]>("/memories").then(setItems);
  useEffect(() => { load(); }, []);
  return (
    <>
      <Section title="Memory" desc="Facts the assistant saves when you ask it to remember something. Relevant ones are added to each chat. Incognito chats never read or change them.">
        <div className="flex items-center gap-3 text-sm">
          <Toggle checked={prefs.memory_enabled} onChange={(v) => save({ memory_enabled: v })} label="Use memory" />
          <span>{prefs.memory_enabled ? "Memory is on" : "Memory is off: nothing is read or saved"}</span>
        </div>
      </Section>
      <Section title={`Saved memories${items ? ` (${items.length})` : ""}`}>
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!adding.trim()) return; await post("/memories", { text: adding.trim() }); setAdding(""); load(); }}>
          <input className={input} placeholder="Add a memory, e.g. “I prefer metric units”" aria-label="New memory" value={adding} onChange={(e) => setAdding(e.target.value)} />
          <button type="submit" className={primary} disabled={!adding.trim()}><Plus size={14} /> Add</button>
        </form>
        <ul className="divide-y divide-line rounded-lg border border-line" data-testid="memory-list">
          {items?.map((m) => (
            <li key={m.id} className="flex items-start gap-2 px-3 py-2 text-sm">
              {editing === m.id ? (
                <form className="flex flex-1 gap-2" onSubmit={async (e) => { e.preventDefault(); await patch(`/memories/${m.id}`, { text: draft }); setEditing(null); load(); }}>
                  <input autoFocus className={input} value={draft} aria-label="Edit memory" onChange={(e) => setDraft(e.target.value)} />
                  <button type="submit" className={primary}>Save</button>
                  <button type="button" className={btn} onClick={() => setEditing(null)}><X size={14} /></button>
                </form>
              ) : (
                <>
                  <span className="min-w-0 flex-1">{m.text}</span>
                  <span className="shrink-0 text-xs text-faint">{new Date(m.updated_at * 1000).toLocaleDateString()}</span>
                  <button type="button" aria-label="Edit memory" className="rounded p-1 text-muted hover:bg-surface-2" onClick={() => { setEditing(m.id); setDraft(m.text); }}><Pencil size={14} /></button>
                  <button type="button" aria-label="Delete memory" className="rounded p-1 text-muted hover:bg-danger-soft hover:text-danger" onClick={async () => { await del(`/memories/${m.id}`); load(); }}><Trash2 size={14} /></button>
                </>
              )}
            </li>
          ))}
          {items && !items.length && <li className="px-3 py-4 text-sm text-faint">No memories yet. Say “remember that …” in any chat.</li>}
        </ul>
      </Section>
    </>
  );
}

interface ToolInfo { name: string; description: string; enabled: boolean; policy: string; source: string; network: boolean; side_effect: boolean }
interface McpServer { name: string; status: string; error: string | null; transport: string; tools: string[] }

function ToolsSection() {
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [config, setConfig] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setTools(await get<ToolInfo[]>("/tools"));
    setServers(await get<McpServer[]>("/mcp"));
    const c = await get<{ mcpServers: object }>("/mcp/config");
    setConfig(JSON.stringify({ mcpServers: c.mcpServers }, null, 2));
  };
  useEffect(() => { load(); }, []);
  const setTool = async (name: string, body: Partial<ToolInfo>) => { await patch(`/tools/${name}`, body); load(); refreshCapabilities(); };
  return (
    <>
      <Section title="Tools" desc="What the assistant may use. “Ask first” shows an Allow / Deny prompt in the chat before the tool runs.">
        <ul className="divide-y divide-line rounded-lg border border-line">
          {tools?.map((t) => (
            <li key={t.name} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <Toggle checked={t.enabled} onChange={(v) => setTool(t.name, { enabled: v })} label={`Enable ${t.name}`} />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-xs font-medium">{t.name}{t.network && <span className="ml-2 rounded bg-warn-soft px-1.5 py-0.5 font-sans text-[11px] text-warn">internet</span>}{t.source === "mcp" && <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 font-sans text-[11px] text-muted">MCP</span>}</div>
                <div className="truncate text-xs text-muted" title={t.description}>{t.description}</div>
              </div>
              <select aria-label={`Policy for ${t.name}`} value={t.policy} onChange={(e) => setTool(t.name, { policy: e.target.value })}
                className="rounded-md border border-line bg-surface px-2 py-1 text-xs">
                <option value="allow">Allow</option><option value="confirm">Ask first</option><option value="deny">Block</option>
              </select>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="MCP servers" desc="Connect local tools over the Model Context Protocol (stdio command or HTTP URL). Saved to config/mcp.json.">
        <ul className="space-y-1 text-sm">
          {servers.map((s) => (
            <li key={s.name} className="flex items-center gap-2">
              <span className={clsx("h-2 w-2 rounded-full", s.status === "ready" ? "bg-accent" : s.status === "error" ? "bg-danger" : "bg-surface-3")} />
              <span className="font-medium">{s.name}</span><span className="text-xs text-faint">{s.transport} · {s.status}{s.tools.length ? ` · ${s.tools.length} tools` : ""}</span>
              {s.error && <span className="truncate text-xs text-danger" title={s.error}>{s.error}</span>}
            </li>
          ))}
          {!servers.length && <li className="text-faint">No servers configured.</li>}
        </ul>
        <textarea value={config} onChange={(e) => setConfig(e.target.value)} rows={10} spellCheck={false} aria-label="MCP configuration" className={clsx(input, "font-mono text-xs")} />
        {err && <p className="text-sm text-danger">{err}</p>}
        <div className="flex gap-2">
          <button type="button" className={primary} disabled={busy} onClick={async () => {
            setErr(null);
            setBusy(true);
            try {
              await api("PUT", "/mcp/config", JSON.parse(config));
              await load();
            } catch (e) {
              setErr(e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : String((e as Error).message));
            } finally {
              setBusy(false);
            }
          }}>{busy ? <Loader2 size={14} className="animate-spin" /> : null}Save & reconnect</button>
          <button type="button" className={btn} onClick={async () => { await post("/mcp/reload"); load(); }}><RefreshCw size={14} /> Reconnect</button>
        </div>
      </Section>
    </>
  );
}

function Models({ prefs, save }: { prefs: Prefs; save: (p: Partial<Prefs>) => Promise<void> }) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  useEffect(() => { get<ModelStatus>("/models").then(setStatus); }, []);
  const { setModelsPanel } = useUi();
  const ctxMax = status?.llms.find((l) => l.id === status.defaults.llm)?.context_length ?? 65536;
  return (
    <>
      <Section title="Models" desc="Loaded models, memory and speed.">
        <button type="button" className={btn} onClick={() => setModelsPanel(true)}>Open model status</button>
      </Section>
      <Section title="Generation">
        <Slider label="Temperature" value={prefs.temperature} min={0} max={1.5} step={0.05} onChange={(v) => save({ temperature: v })} />
        <Slider label="Max reply length (tokens)" value={prefs.max_tokens} min={256} max={16384} step={256} onChange={(v) => save({ max_tokens: v })} />
        <Slider label="Context window (tokens)" value={prefs.context_tokens ?? ctxMax} min={4096} max={ctxMax} step={1024}
          onChange={(v) => save({ context_tokens: v >= ctxMax ? null : v })} format={(v) => (v >= ctxMax ? `${v} (model max)` : String(v))} />
        <Slider label="Video frame budget" value={prefs.video_frame_budget} min={4} max={32} step={1} onChange={(v) => save({ video_frame_budget: v })}
          format={(v) => `${v} keyframes`} />
      </Section>
      {status?.video && status.video.some((m) => m.installed) && (
        <Section title="Video generation" desc="Ask in any chat, e.g. “make a 4-second video of a fox running through snow”. Name a model to choose it; attach an image to animate it.">
          <ul className="space-y-4 text-sm" data-testid="video-models">
            {/* Only engines this Mac has; the installer adds others (run it again to choose). */}
            {status.video.filter((m) => m.installed).map((m) => (
              <li key={m.id} className="space-y-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{m.display_name}</span>
                  {m.default && <span className="rounded bg-accent-soft px-1.5 text-xs text-accent">default</span>}
                  {m.audio && <span className="text-xs text-faint">with sound</span>}
                </div>
                <VideoLength model={m} status={status} lengths={prefs.video_max_seconds ?? {}}
                  save={(lengths) => save({ video_max_seconds: lengths })} />
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

function Voice({ prefs, save }: { prefs: Prefs; save: (p: Partial<Prefs>) => Promise<void> }) {
  const [voices, setVoices] = useState<{ id: string; name: string; language: string; gender: string }[]>([]);
  const [playing, setPlaying] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const v = prefs.voice;
  useEffect(() => { get<typeof voices>("/voices").then(setVoices); }, []);
  const setV = (p: Partial<Prefs["voice"]>) => save({ voice: { ...v, ...p } });
  const preview = async () => {
    setPlaying(true);
    try {
      const t = getToken();
      const res = await fetch(apiUrl("/tts"), { method: "POST", headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        body: JSON.stringify({ text: "Hi! This is how I sound when I read replies aloud.", voice: v.tts_voice, speed: v.speed }) });
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      audio.onended = () => setPlaying(false);
      await audio.play();
    } catch {
      setPlaying(false);
    }
  };
  useEffect(() => {
    if (!capturing) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      setCapturing(false);
      if (e.key !== "Escape") setV({ push_to_talk_key: e.code });
    };
    window.addEventListener("keydown", onKey, { once: true, capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  });
  return (
    <>
      <Section title="Speech recognition">
        <div className="flex flex-wrap gap-2">
          {[["parakeet-v2", "Parakeet (English, fastest)"], ["whisper-turbo", "Whisper turbo (99 languages)"]].map(([id, label]) => (
            <button key={id} type="button" onClick={() => setV({ stt: id })} aria-pressed={v.stt === id}
              className={clsx(btn, v.stt === id && "border-accent bg-accent-soft text-accent")}>{label}</button>
          ))}
        </div>
        <Slider label="Voice detection sensitivity" value={v.vad_sensitivity} min={0} max={1} step={0.05} onChange={(x) => setV({ vad_sensitivity: x })}
          format={(x) => (x < 0.35 ? "less sensitive, waits longer" : x > 0.65 ? "more sensitive, replies sooner" : "balanced")} />
        <div className="flex items-center gap-3 text-sm">
          <span>Push-to-talk key (hold to dictate)</span>
          <button type="button" className={btn} onClick={() => setCapturing(true)}>{capturing ? "Press a key…" : <kbd className="font-mono text-xs">{keyLabel(v.push_to_talk_key)}</kbd>}</button>
        </div>
      </Section>
      <Section title="Spoken replies">
        <div className="flex items-center gap-2">
          <select aria-label="Voice" value={v.tts_voice} onChange={(e) => setV({ tts_voice: e.target.value })} className={clsx(input, "max-w-xs")}>
            {voices.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.language}{x.gender ? ` · ${x.gender}` : ""}</option>)}
          </select>
          <button type="button" className={btn} onClick={preview} disabled={playing}>{playing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Preview</button>
        </div>
        <Slider label="Speaking speed" value={v.speed} min={0.7} max={1.5} step={0.05} onChange={(x) => setV({ speed: x })} format={(x) => `${x.toFixed(2)}×`} />
      </Section>
    </>
  );
}

export function keyLabel(code: string): string {
  const names: Record<string, string> = { AltRight: "Right ⌥ Option", AltLeft: "Left ⌥ Option", MetaRight: "Right ⌘", ControlRight: "Right ⌃", ShiftRight: "Right ⇧", Space: "Space" };
  return names[code] ?? code.replace(/^Key/, "").replace(/^Digit/, "");
}

interface RemoteInfo {
  remote_access: boolean;
  proxied: boolean;
  web_access?: boolean;
  paused_by_web?: boolean;
  token?: string;
  tailscale: {
    installed: boolean; running: boolean; dns_name: string | null; serving: boolean; url: string | null;
    phones?: { name: string; os: string; online: boolean }[];
  };
}

const APP_STORE = "https://apps.apple.com/us/app/tailscale/id1470499037";
const GOOGLE_PLAY = "https://play.google.com/store/apps/details?id=com.tailscale.ipn";
const TAILSCALE_MAC = "https://tailscale.com/download/mac";

/** A QR code drawn in the page (the library is bundled, so it works offline). Always dark on white. */
function QrCode({ value, label, size = 132 }: { value: string; label: string; size?: number }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    QRCode.toString(value, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#17171a", light: "#ffffff" } })
      .then(setSvg).catch(() => setSvg(""));
  }, [value]);
  return <div role="img" aria-label={label} style={{ width: size, height: size }}
    className="shrink-0 overflow-hidden rounded-lg border border-line bg-white p-1 [&>svg]:h-full [&>svg]:w-full"
    dangerouslySetInnerHTML={{ __html: svg }} />;
}

/** One numbered setup step; the number turns into a green tick when it's done. */
function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface p-5" aria-label={`Step ${n}: ${title}${done ? " (done)" : ""}`}>
      <h2 className="flex items-center gap-2.5 font-semibold">
        <span className={clsx("grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold",
          done ? "bg-ok text-white" : "bg-surface-3 text-fg")}>{done ? <Check size={14} strokeWidth={3} /> : n}</span>
        {title}
      </h2>
      <div className="space-y-3 pl-8 text-sm">{children}</div>
    </section>
  );
}

function Remote() {
  const [info, setInfo] = useState<RemoteInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [showQr, setShowQr] = useState(true);
  const web = useWebAccess();
  const [copiedToken, copyToken] = useCopy();
  const [copiedUrl, copyUrl] = useCopy();
  const [shareNote, setShareNote] = useState<string | null>(null);
  const shareBtn = useRef<HTMLButtonElement>(null);
  const load = () => get<RemoteInfo>("/remote").then(setInfo);
  const recheck = async () => { setChecking(true); try { await load(); } finally { setChecking(false); } };
  // The Web access switch (in the sidebar) can turn phone access off and on: follow it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [web.on]);
  // While setup isn't finished, look again every few seconds so the steps tick themselves.
  const pending = !!info && !info.proxied && (!info.tailscale.running || !(info.tailscale.phones ?? []).some((p) => p.online));
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [pending]);
  if (!info) return <Loader2 className="animate-spin" />;
  const ts = info.tailscale;
  const macDone = ts.installed && ts.running;
  const phones = ts.phones ?? [];
  const phoneOnline = phones.find((p) => p.online);
  const link = info.token && ts.url ? signInLink(ts.url, info.token) : null;
  const webOff = info.web_access === false || !web.on;
  const toggle = async (on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setInfo(await post<RemoteInfo>("/remote", { enabled: on }));
    } catch (e) {
      setError(String((e as Error).message));
      load();
    } finally {
      setBusy(false);
    }
  };
  const checkAgain = (
    <button type="button" className={btn} onClick={recheck} disabled={checking}>
      {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Check again
    </button>
  );

  return (
    <>
      <p className="text-sm text-muted">Your Mac keeps running the AI; your phone connects to it over Tailscale, a free private network
        between your own devices. Nothing is opened to the public internet, and a login token protects access.</p>
      {info.proxied && <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted">You're connected from another device. Phone access and the token can only be changed on the Mac.</p>}

      {!info.proxied && (
        <Step n={1} title="Set up Tailscale on your Mac" done={macDone}>
          {macDone ? (
            <p className="text-muted">Tailscale is running on this Mac{ts.dns_name ? <> as <span className="font-mono text-xs text-fg">{ts.dns_name}</span></> : null}.</p>
          ) : (
            <>
              <ol className="list-decimal space-y-1 pl-5 text-muted">
                <li>{ts.installed ? "Tailscale is installed." : <>Download Tailscale for Mac and open it.</>}</li>
                <li>Click the Tailscale icon in the menu bar and sign in, with Google, Apple, Microsoft or GitHub. It's free for personal use.</li>
                <li>Come back here: this step ticks itself once Tailscale is connected.</li>
              </ol>
              <div className="flex flex-wrap gap-2">
                {!ts.installed && <a className={primary} href={TAILSCALE_MAC} target="_blank" rel="noreferrer"><Download size={14} /> Download Tailscale for Mac</a>}
                {checkAgain}
              </div>
            </>
          )}
        </Step>
      )}

      {!info.proxied && (
        <Step n={2} title="Set up Tailscale on your phone" done={!!phoneOnline}>
          {phoneOnline ? (
            <p className="text-muted">Found <span className="font-medium text-fg">{phoneOnline.name}</span> ({phoneOnline.os === "iOS" ? "iPhone" : "Android"}) on your Tailscale network.</p>
          ) : phones.length > 0 ? (
            <p className="text-muted">{phones[0].name} is on your Tailscale network but offline. Open Tailscale on the phone and switch it on.</p>
          ) : null}
          <details open={!phoneOnline} className="group">
            <summary className="cursor-pointer text-muted hover:text-fg">{phoneOnline ? "Show setup steps" : "How to set it up"}</summary>
            <div className="mt-3 flex flex-wrap gap-5">
              <figure className="flex flex-col items-center gap-1.5">
                <QrCode value={APP_STORE} label="QR code: Tailscale on the App Store" />
                <figcaption className="text-xs"><a className="underline" href={APP_STORE} target="_blank" rel="noreferrer">iPhone and iPad</a></figcaption>
              </figure>
              <figure className="flex flex-col items-center gap-1.5">
                <QrCode value={GOOGLE_PLAY} label="QR code: Tailscale on Google Play" />
                <figcaption className="text-xs"><a className="underline" href={GOOGLE_PLAY} target="_blank" rel="noreferrer">Android</a></figcaption>
              </figure>
              <ol className="min-w-56 flex-1 list-decimal space-y-1 pl-5 text-muted">
                <li>Point your phone's camera at a code and install Tailscale.</li>
                <li>Open it and sign in with the <b>same account</b> you used on this Mac.</li>
                <li>Allow the VPN configuration when asked, and leave Tailscale switched on.</li>
              </ol>
            </div>
          </details>
          {!phoneOnline && checkAgain}
        </Step>
      )}

      <Step n={3} title="Use it from your phone" done={info.remote_access && !!ts.url && ts.serving}>
        <div className="flex items-center gap-3">
          {busy ? <Loader2 size={20} className="animate-spin text-muted" /> : (
            <span className={clsx(webOff && "pointer-events-none opacity-40")} aria-disabled={webOff}>
              <Toggle checked={info.remote_access} label="Allow access from my phone"
                onChange={(on) => { if (!info.proxied && !webOff && (!on || macDone)) toggle(on); }} />
            </span>
          )}
          <span><b>Allow access from my phone</b> {info.remote_access ? <span className="text-accent">· on</span> : <span className="text-muted">· off</span>}</span>
        </div>
        {webOff ? (
          <p className="rounded-lg bg-surface-2 px-3 py-2 text-muted" data-testid="phone-web-off">
            {info.paused_by_web
              ? <>Phone access is off because <b>Web access</b> is off. It comes back on by itself when you turn Web access on (above New chat).</>
              : <><b>Web access</b> is off, so phone access can't be turned on. Turn on Web access (above New chat) first.</>}
          </p>
        ) : !macDone && !info.remote_access && <p className="text-muted">Finish step 1 first.</p>}
        {error && <p role="alert" className="whitespace-pre-wrap rounded-lg bg-danger-soft px-3 py-2 text-danger">{error}</p>}
        {info.remote_access && ts.running && !ts.serving && (
          <p className="text-warn">Tailscale isn't forwarding to this app right now. Turn the switch off and on again.</p>
        )}
        {info.remote_access && ts.url && (
          <>
            {link ? (
              <>
                <div>
                  <div className="mb-1 flex items-center gap-2 font-medium">
                    Scan with your phone's camera
                    <button type="button" className="text-xs font-normal text-muted underline" aria-expanded={showQr} onClick={() => setShowQr((x) => !x)}>
                      {showQr ? "Hide code" : "Show code"}
                    </button>
                  </div>
                  {showQr && (
                    <div className="flex items-center gap-4">
                      <QrCode value={link} label="QR code: sign-in link for your phone" size={176} />
                      <p className="max-w-xs text-xs text-muted">Point your phone's camera at the code and tap the link: GoRunRun Local AI opens, signed in, with no token to type. The code contains your login token, so don't share a photo of it.</p>
                    </div>
                  )}
                </div>
                <div>
                  <div className="mb-1 font-medium">Or send yourself the sign-in link</div>
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="rounded-lg border border-line bg-bg px-3 py-1.5 font-mono text-xs" data-testid="remote-url">
                      {ts.url}<span className="text-faint">/#token=••••••</span>
                    </code>
                    <button ref={shareBtn} type="button" className={btn} onClick={async () => {
                      const r = await shareLink(link, "GoRunRun Local AI", shareBtn.current);
                      setShareNote(r === "copied" ? "Link copied. Paste it into Messages or Mail to send it to your phone." : null);
                    }}><Share size={14} /> Share</button>
                    <button type="button" className={btn} onClick={() => copyUrl(link)}>
                      {copiedUrl ? <Check size={14} /> : <Copy size={14} />} Copy link
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-muted">{shareNote ?? "Send it with AirDrop, Messages or Mail. Opening it signs in by itself."}</p>
                </div>
              </>
            ) : (
              <div>
                <div className="mb-1 font-medium">Address</div>
                <code className="rounded-lg border border-line bg-bg px-3 py-1.5 font-mono text-xs" data-testid="remote-url">{ts.url}</code>
              </div>
            )}
            <ol className="list-decimal space-y-1 pl-5 text-muted">
              <li>Make sure Tailscale is on, on your phone.</li>
              <li>Add it to your home screen to use it like an app.</li>
            </ol>
            <div className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-warn">
              <TriangleAlert size={16} className="mt-0.5 shrink-0" />
              <p><b>Sharing with family or friends?</b> Anyone with this link can use your assistant and see all your chats, memories and files, just as you can. They also need to join your Tailscale network, or you share this Mac with them from Tailscale's admin console. To take access back, make a new token below: it signs out everyone using the old link.</p>
            </div>
            <p className="text-muted">Your Mac must be on and awake with GoRunRun Local AI running. In the Mac app, turn on <b>Keep Running in Background</b> so it stays available after you close the window.</p>
          </>
        )}
        {info.token && (
          <div className="space-y-1">
            <div className="font-medium">Login token</div>
            <div className="flex flex-wrap items-center gap-2">
              <code className="rounded-lg border border-line bg-bg px-3 py-1.5 font-mono text-xs">{reveal ? info.token : "•".repeat(24)}</code>
              <button type="button" className={btn} onClick={() => setReveal((x) => !x)}>{reveal ? "Hide" : "Show"}</button>
              <button type="button" className={btn} onClick={() => copyToken(info.token!)}>{copiedToken ? <Check size={14} /> : <Copy size={14} />} Copy</button>
              <button type="button" className={btn} onClick={async () => { await post("/remote/token/rotate"); load(); }}><RefreshCw size={14} /> New token</button>
            </div>
            <p className="text-xs text-faint">A new token signs out every device using the old one, and old sign-in links stop working.</p>
          </div>
        )}
      </Step>
    </>
  );
}

interface UpdateCheck {
  ok: boolean;
  current: string;
  latest?: string;
  update_available?: boolean;
  releases?: { version: string; date: string | null; notes: string }[];
  install_command?: string;
  error?: string;
}

/** Checks GitHub only when asked: the app never phones home by itself. */
function Updates() {
  const [version, setVersion] = useState<string | null>(null);
  const [result, setResult] = useState<UpdateCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [copied, copy] = useCopy();
  const web = useWebAccess();
  useEffect(() => { get<{ version: string }>("/version").then((v) => setVersion(v.version)).catch(() => undefined); }, []);
  const check = async () => {
    setChecking(true);
    try {
      setResult(await get<UpdateCheck>("/updates/check"));
    } catch (e) {
      setResult({ ok: false, current: version ?? "", error: String((e as Error).message) });
    } finally {
      setChecking(false);
    }
  };
  return (
    <Section title="Updates" desc="Checking reads one public file on GitHub. Nothing about you or your chats is sent, and the app never checks on its own.">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span>You have version <b data-testid="app-version">{version ?? "…"}</b></span>
        <button type="button" className={primary} onClick={check} disabled={checking || !web.on}
          aria-describedby={web.on ? undefined : "updates-offline"}>
          {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Check for updates
        </button>
      </div>
      {!web.on && (
        <p id="updates-offline" className="text-sm text-muted">
          Web access is off, so the app doesn't go online. Turn on <b>Web access</b> (above New chat) to check for updates.
        </p>
      )}
      {result && !result.ok && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{result.error}</p>
      )}
      {result?.ok && !result.update_available && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <span className="grid h-5 w-5 place-items-center rounded-full bg-ok text-white"><Check size={13} strokeWidth={3} /></span>
          You're up to date. Version {result.latest} is the latest.
        </p>
      )}
      {result?.ok && result.update_available && (
        <div className="space-y-4 text-sm">
          <p role="status" className="font-medium">Version {result.latest} is available.</p>
          <div className="space-y-3">
            <h3 className="font-semibold">What's new</h3>
            {result.releases!.map((r) => (
              <article key={r.version} className="rounded-lg border border-line bg-bg px-4 py-3">
                <h4 className="font-semibold">Version {r.version}{r.date && <span className="ml-2 text-xs font-normal text-muted">{r.date}</span>}</h4>
                <div className="mt-1 text-sm"><Markdown text={r.notes} /></div>
              </article>
            ))}
          </div>
          <div className="space-y-2">
            <h3 className="font-semibold">How to update</h3>
            <ol className="list-decimal space-y-1 pl-5 text-muted">
              <li>Open <b>Terminal</b> (press ⌘ Space, type "Terminal", press Return).</li>
              <li>Paste this command and press Return. It's the same one you installed with:</li>
            </ol>
            <div className="flex items-start gap-2">
              <code className="flex-1 rounded-lg border border-line bg-bg px-3 py-2 font-mono text-xs break-all">{result.install_command}</code>
              <button type="button" className={btn} onClick={() => copy(result.install_command!)}>{copied ? <Check size={14} /> : <Copy size={14} />} Copy</button>
            </div>
            <p className="text-muted">It asks the same questions as before, then updates in place. Your chats, settings and downloaded models are kept. When it finishes, the app reopens with the new version.</p>
            <p className="text-xs text-faint">Installed from source instead? Run <code>git pull</code> in the project folder, then <code>make deps app-frontend</code>, and restart the app.</p>
          </div>
        </div>
      )}
    </Section>
  );
}

interface CleanupResult { categories: Record<string, { count: number; bytes: number }>; total_bytes: number; cleared: boolean }

type ClutterItem = { id: string; label: string; hint: string };

const AI_ITEMS: ClutterItem[] = [
  { id: "videos", label: "Videos", hint: "Clips from video creation and animated photos." },
  { id: "files", label: "Images and files", hint: "Charts, pictures and documents it made by running code." },
  { id: "artifacts", label: "Mini apps and diagrams", hint: "What opens in the side panel: apps, charts, pages and diagrams." },
  { id: "temp", label: "Temporary files and caches", hint: "Work files and processed copies. They're made again when needed." },
];
const UPLOAD_ITEMS: ClutterItem[] = [
  { id: "my_photos", label: "Photos and screenshots", hint: "Pictures you attached, pasted or captured." },
  { id: "my_videos", label: "Videos", hint: "Video clips you attached or recorded." },
  { id: "my_files", label: "Documents and other files", hint: "PDFs, Word and Excel files, code and other files you attached." },
  { id: "my_voice", label: "Voice messages", hint: "Recordings you sent. What they said stays in the chat as text." },
];

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** One group in Settings → Clear clutter, with its own choices, date range, preview and Clear button. */
function ClutterGroup({ id, title, desc, items }: { id: string; title: string; desc: string; items: ClutterItem[] }) {
  const [cats, setCats] = useState<string[]>(items.map((c) => c.id));
  const [range, setRange] = useState<"all" | "dates">("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [preview, setPreview] = useState<CleanupResult | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const showToast = useChat((s) => s.showToast);
  // Dates are local days: "to" includes the whole of that day.
  const body = (dry: boolean) => ({
    categories: cats, dry_run: dry,
    since: range === "dates" && from ? new Date(`${from}T00:00:00`).getTime() / 1000 : null,
    until: range === "dates" && to ? new Date(`${to}T00:00:00`).getTime() / 1000 + 86400 : null,
  });
  const badRange = range === "dates" && !!from && !!to && from > to;
  const load = () => {
    if (!cats.length || badRange) { setPreview(null); return; }
    post<CleanupResult>("/cleanup", body(true))
      .then((r) => { setPreview(r); setFailed(false); })
      .catch(() => { setPreview(null); setFailed(true); });
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [cats.join(), range, from, to]);
  const count = preview ? Object.values(preview.categories).reduce((n, c) => n + c.count, 0) : 0;
  const clear = async () => {
    setConfirm(false);
    setBusy(true);
    try {
      const r = await post<CleanupResult>("/cleanup", body(false));
      showToast(`Cleared ${fmtBytes(r.total_bytes)}`);
      load();
    } catch (e) {
      showToast(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={`${id}-title`} className="space-y-3 rounded-xl border border-line bg-surface p-5">
      <div>
        <h2 id={`${id}-title`} className="font-semibold">{title}</h2>
        <p className="mt-0.5 text-sm text-muted">{desc}</p>
      </div>
      <fieldset className="space-y-2">
        <legend className="mb-1 text-sm font-medium">What to clear</legend>
        {items.map((c) => {
          const info = preview?.categories[c.id];
          return (
            <label key={c.id} className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input type="checkbox" className="mt-1 accent-[var(--accent)]" checked={cats.includes(c.id)} aria-label={`${title}: ${c.label}`}
                onChange={(e) => setCats((x) => (e.target.checked ? [...x, c.id] : x.filter((y) => y !== c.id)))} />
              <span className="flex-1">
                <span className="font-medium">{c.label}</span>
                {info && cats.includes(c.id) && <span className="ml-2 text-xs text-muted">{info.count} · {fmtBytes(info.bytes)}</span>}
                <span className="block text-xs text-muted">{c.hint}</span>
              </span>
            </label>
          );
        })}
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="mb-1 text-sm font-medium">From when</legend>
        <label className="flex items-center gap-2 text-sm"><input type="radio" name={`${id}-range`} checked={range === "all"} onChange={() => setRange("all")} /> Everything</label>
        <label className="flex items-center gap-2 text-sm"><input type="radio" name={`${id}-range`} checked={range === "dates"} onChange={() => setRange("dates")} /> Date range</label>
        {range === "dates" && (
          <div className="flex flex-wrap items-center gap-2 pl-6 text-sm">
            <label className="flex items-center gap-1.5">From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={clsx(input, "w-auto")} /></label>
            <label className="flex items-center gap-1.5">To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={clsx(input, "w-auto")} /></label>
            {!from && !to && <span className="text-xs text-muted">Leave a date empty for no limit on that side.</span>}
            {badRange && <span className="text-xs text-danger">"From" is after "To".</span>}
          </div>
        )}
      </fieldset>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" disabled={busy || !preview || count === 0} onClick={() => setConfirm(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-danger px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-40">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />} Clear
        </button>
        <span role="status" data-testid={`${id}-summary`} className="text-muted">
          {!cats.length ? "Choose what to clear." : failed ? "Couldn't count what can be cleared. Quit and reopen the app, then try again."
            : !preview ? "Counting…" : count === 0 ? "Nothing to clear."
            : `${count} item${count === 1 ? "" : "s"}, about ${fmtBytes(preview.total_bytes)}.`}
        </span>
      </div>
      {confirm && preview && (
        <ConfirmDialog title={`Clear ${title.toLowerCase()}?`} danger confirmLabel={`Clear ${fmtBytes(preview.total_bytes)}`}
          body={<>This removes {count} item{count === 1 ? "" : "s"} ({fmtBytes(preview.total_bytes)}) {range === "all" ? "from all time" : "in the dates you chose"}. It can't be undone. The text of your chats is kept.</>}
          onConfirm={clear} onClose={() => setConfirm(false)} />
      )}
    </section>
  );
}

/** Settings → Clear clutter: two separate groups, what the AI made and what you added. */
function ClearClutter() {
  return (
    <>
      <p className="text-sm text-muted">Videos, pictures and files add up over time. Clear them here to free space on your Mac. The text of your
        chats is always kept; where something was cleared, the chat says so. Files you added to a Project stay, because the project uses them.</p>
      <ClutterGroup id="ai" title="Generated by AI" items={AI_ITEMS}
        desc="What the assistant made for you. You can ask again to make any of it anew." />
      <ClutterGroup id="uploads" title="Your uploads" items={UPLOAD_ITEMS}
        desc="What you added to chats. Once cleared, the assistant can't look at them again, so keep copies of anything you need." />
    </>
  );
}

export function SettingsPage() {
  const { section = "general" } = useParams();
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const { sidebar, setSidebar } = useUi();
  const showToast = useChat((s) => s.showToast);
  useEffect(() => { get<Prefs>("/settings").then(setPrefs); }, []);
  const save = async (p: Partial<Prefs>) => {
    try {
      setPrefs(await patch<Prefs>("/settings", p));
    } catch (e) {
      showToast(String((e as Error).message));
    }
  };
  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        {!sidebar && <button type="button" onClick={() => setSidebar(true)} aria-label="Show sidebar" className="rounded-md p-1.5 text-muted hover:bg-surface-2"><PanelLeftOpen size={17} /></button>}
        <h1 className="text-sm font-medium">Settings</h1>
      </header>
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto border-line p-3 md:w-48 md:flex-col md:border-r">
          {SECTIONS.map(([id, label]) => (
            <NavLink key={id} to={`/settings/${id}`} className={({ isActive }) => clsx("whitespace-nowrap rounded-lg px-3 py-1.5 text-sm",
              isActive || (id === "general" && section === "general") ? "bg-surface-3 font-medium" : "text-muted hover:bg-surface-2")}>{label}</NavLink>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-2xl space-y-4 px-4 py-6">
            {!prefs ? <Loader2 className="animate-spin" /> : (
              <>
                {section === "general" && <General prefs={prefs} save={save} />}
                {section === "styles" && <Styles prefs={prefs} save={save} />}
                {section === "memory" && <MemorySection prefs={prefs} save={save} />}
                {section === "tools" && <ToolsSection />}
                {section === "models" && <Models prefs={prefs} save={save} />}
                {section === "voice" && <Voice prefs={prefs} save={save} />}
                {section === "remote" && <Remote />}
                {section === "setup" && <SetupSummary collapsible={false} />}
                {section === "storage" && <ClearClutter />}
                {section === "updates" && <Updates />}
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
