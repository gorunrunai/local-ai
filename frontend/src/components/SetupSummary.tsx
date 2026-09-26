import clsx from "clsx";
import { AlertTriangle, Check, ChevronDown, Cpu, X } from "lucide-react";
import { useState } from "react";
import { useCapabilities } from "../lib/capabilities";
import { useOnline } from "../lib/online";
import { InfoTip } from "./InfoTip";

const OPEN_KEY = "gorunrun.setupSummary.open";

function remembered(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== "0";
  } catch {
    return true;
  }
}

/** This Mac, what's installed, what works and what doesn't (limitations in red, explained on hover). */
export function SetupSummary({ collapsible = true }: { collapsible?: boolean }) {
  const caps = useCapabilities();
  const online = useOnline();
  const [open, setOpen] = useState(() => !collapsible || remembered());
  if (!caps) return null;
  const { machine, tested } = caps;
  const toggle = () => {
    setOpen(!open);
    try { localStorage.setItem(OPEN_KEY, open ? "0" : "1"); } catch { /* ignore */ }
  };
  const chip = machine.chip.replace(/^Apple /, "");

  return (
    <section aria-label="Your setup" className="w-full rounded-xl border border-line bg-surface text-left text-sm">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <Cpu size={16} className="shrink-0 text-muted" />
        <span className="min-w-0 flex-1">
          <span className="font-medium">{chip}</span>
          <span className="text-muted"> · {machine.memory_gb} GB · macOS {machine.macos}</span>
        </span>
        {tested ? (
          <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent">Tested</span>
        ) : (
          <a href={caps.report_url} target="_blank" rel="noreferrer"
            className="shrink-0 rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn hover:underline">Untested: help us test</a>
        )}
        {collapsible && (
          <button type="button" onClick={toggle} aria-expanded={open} aria-label={open ? "Hide setup details" : "Show setup details"}
            className="shrink-0 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg">
            <ChevronDown size={16} className={clsx("transition-transform", open && "rotate-180")} />
          </button>
        )}
      </div>

      {open && (
        <div className="space-y-3 border-t border-line px-4 py-3">
          <p className="text-muted">
            {caps.installed.map((m, i) => (
              <span key={m.role + m.name}>{i > 0 && " · "}<span className="text-fg">{m.name}</span> ({m.role.toLowerCase()})</span>
            ))}
          </p>
          <ul aria-label="What you can do" className="flex flex-wrap gap-1.5">
            {caps.features.map((x) => (x.id === "web_search" && x.available && !online
              ? { ...x, available: false, reason: "This Mac is offline. Web search works again when it reconnects; everything else works now." } : x)).map((f) => (
              <li key={f.id} className={clsx("flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                f.available ? "border-line" : "border-dashed border-line text-faint")}>
                {f.available ? <Check size={12} className="shrink-0 text-accent" /> : <X size={12} className="shrink-0" />}
                <span className={clsx(!f.available && "line-through")}>{f.label}</span>
                {!f.available && f.reason && <InfoTip text={f.reason} label={`Why "${f.label}" isn't available`} />}
              </li>
            ))}
          </ul>
          {caps.limitations.length > 0 && (
            <ul aria-label="Limitations" className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
              {caps.limitations.map((l) => (
                <li key={l.text} className="flex items-start gap-1.5 text-danger">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{l.text} <InfoTip text={l.detail} label={`About: ${l.text}`} /></span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
