import clsx from "clsx";
import { Globe } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { refreshCapabilities, useCapabilities } from "../lib/capabilities";
import { useChat } from "../store/chat";

interface WebAccessResult { enabled: boolean; phone_access: boolean; phone_changed: boolean; phone_paused: boolean; note: string | null }

/** Web search and reading web pages, on or off together: the only features that go online. */
export function useWebAccess() {
  const caps = useCapabilities();
  const [busy, setBusy] = useState(false);
  const showToast = useChat((s) => s.showToast);
  const set = async (enabled: boolean) => {
    // On the phone itself, phone access is this connection: turning web access off would cut it.
    if (!enabled && location.hostname.endsWith(".ts.net") &&
        !window.confirm("Turning off Web access also turns off phone access, which disconnects this phone. Turn it off?")) return;
    setBusy(true);
    try {
      const r = await api<WebAccessResult>("/web-access", { method: "PUT", body: JSON.stringify({ enabled }) as BodyInit });
      if (r.note) showToast(r.note);
      else if (r.phone_changed) {
        showToast(r.phone_access ? "Phone access is back on." : "Phone access is off too. It comes back on when you turn Web access on.");
      }
      await refreshCapabilities();                 // every switch and the setup card update together
    } finally {
      setBusy(false);
    }
  };
  return { ready: !!caps, on: caps?.web_access ?? true, busy, set };
}

export function WebAccessSwitch({ label = "Web access: web search and reading web pages" }: { label?: string }) {
  const { on, busy, set } = useWebAccess();
  return (
    <button type="button" role="switch" aria-checked={on} disabled={busy} onClick={() => set(!on)} aria-label={label}
      className={clsx("relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50", on ? "bg-accent" : "bg-surface-3")}>
      <span className={clsx("absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform", on && "translate-x-5")} />
    </button>
  );
}

/** The sidebar row, above "New chat", so it's reachable from every page. */
export function SidebarWebAccess() {
  const { ready, on } = useWebAccess();
  if (!ready) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm"
      title={on ? "Web search and reading web pages are allowed, only when you ask." : "Nothing goes online: web search and reading web pages are off."}>
      <Globe size={16} className={on ? "text-accent" : "text-faint"} aria-hidden />
      <span className="flex-1">Web access <span className="text-xs text-muted">{on ? "on" : "off"}</span></span>
      <WebAccessSwitch label="Web access (sidebar): web search and reading web pages" />
    </div>
  );
}
