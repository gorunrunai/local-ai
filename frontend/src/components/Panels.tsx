import { KeyRound, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { get, post, setToken } from "../api/client";
import type { ModelStatus } from "../api/types";
import { useChat } from "../store/chat";
import { Modal } from "./Modal";

export const SHORTCUTS: [string, string][] = [
  ["⌘ K", "New chat"],
  ["⌘ /", "Focus the message box"],
  ["⌘ ⇧ V", "Voice mode on/off"],
  ["⌘ ⇧ D", "Start/stop dictation"],
  ["Space", "Interrupt the assistant (voice mode)"],
  ["Esc", "Stop generating"],
  ["Enter", "Send"],
  ["⇧ Enter", "New line"],
  ["⌘ V", "Paste an image or screenshot"],
  ["/", "Commands: /think /model /style /project"],
  ["@", "Mention a file or past chat"],
  ["?", "Show this list"],
];

export function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        {SHORTCUTS.map(([k, v]) => (
          <div key={k} className="contents">
            <dt><kbd className="rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{k}</kbd></dt>
            <dd className="text-muted">{v}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}

export function ModelsPanel({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const loadModels = useChat((s) => s.loadModels);
  const refresh = () => get<ModelStatus>("/models").then(setStatus);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);
  const act = async (id: string, action: "load" | "unload") => {
    setBusy(id);
    try {
      await post(`/models/${id}/${action}`);
      await refresh();
      loadModels();
    } finally {
      setBusy(null);
    }
  };
  return (
    <Modal title="Models" onClose={onClose}>
      {!status ? <Loader2 className="animate-spin" /> : (
        <div className="space-y-4 text-sm">
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted">
              <span>Memory in use by models</span>
              <span className="tabular-nums">{status.models_total_gb.toFixed(1)} of {status.budget_gb} GB budget · {status.system.available_gb} GB free on this Mac</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuemin={0} aria-valuemax={status.budget_gb} aria-valuenow={status.models_total_gb} aria-label="Model memory">
              <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (status.models_total_gb / status.budget_gb) * 100)}%` }} />
            </div>
          </div>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {status.llms.filter((l) => l.installed !== false).map((l) => (
              <li key={l.id} className="flex items-center gap-3 px-3 py-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${l.loaded ? "bg-accent" : "bg-surface-3"}`} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{l.display_name}{l.id === status.defaults.llm && <span className="ml-1.5 text-xs font-normal text-faint">default</span>}{l.id === status.defaults.audio_listener && <span className="ml-1.5 text-xs font-normal text-faint">listens to voice messages</span>}</span>
                  <span className="text-xs text-muted tabular-nums">
                    {l.loaded ? `${l.memory_gb ?? "?"} GB` : `~${l.est_memory_gb} GB when loaded`}
                    {l.speed?.decode_tok_s ? ` · ${Math.round(l.speed.decode_tok_s)} tok/s · first token ${l.speed.ttft_s?.toFixed(2)}s` : ""}
                  </span>
                </span>
                <button type="button" disabled={busy === l.id} onClick={() => act(l.id, l.loaded ? "unload" : "load")}
                  className="rounded-md border border-line px-2.5 py-1 text-xs hover:bg-surface-2 disabled:opacity-50">
                  {busy === l.id ? <Loader2 size={12} className="animate-spin" /> : l.loaded ? "Unload" : "Load"}
                </button>
              </li>
            ))}
          </ul>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-faint">Speech, embeddings, reranker</div>
            <ul className="grid grid-cols-2 gap-1 text-xs">
              {status.services.map((s) => (
                <li key={s.id} className="flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${s.loaded ? "bg-accent" : "bg-surface-3"}`} />{s.id} <span className="text-faint">({s.kind})</span></li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Shown on a phone (via Tailscale) until the login token is entered. */
export function TokenGate({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setToken(value.trim());
    try {
      await get("/models");
      onDone();
    } catch {
      setToken(null);
      setError("That token wasn't accepted.");
    }
  };
  return (
    <div className="grid h-full place-items-center p-6">
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="w-full max-w-sm space-y-3 rounded-2xl border border-line bg-surface p-6 shadow-card">
        <KeyRound className="text-accent" />
        <h1 className="text-lg font-semibold">Connect to your Mac</h1>
        <p className="text-sm text-muted">Enter the login token from <b>Settings → Phone access</b> on your Mac, or open the sign-in link or QR code shown there.</p>
        <input id="token" value={value} onChange={(e) => setValue(e.target.value)} autoFocus autoComplete="off" aria-label="Login token"
          className="w-full rounded-lg border border-line bg-bg px-3 py-2 font-mono text-sm outline-none focus:border-accent" />
        {error && <p className="text-sm text-danger">{error}</p>}
        <button type="submit" disabled={!value.trim()} className="w-full rounded-lg bg-accent py-2 text-sm font-medium text-on-accent disabled:opacity-40">Connect</button>
      </form>
    </div>
  );
}
