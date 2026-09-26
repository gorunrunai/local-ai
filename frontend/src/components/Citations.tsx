import { ExternalLink, FileText, MessagesSquare } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import type { Citation } from "../api/types";

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function SourceLine({ c }: { c: Citation }) {
  const Icon = c.url ? ExternalLink : c.conversation_id ? MessagesSquare : FileText;
  const label = (
    <>
      <Icon size={13} className="shrink-0" />
      <span className="truncate">{c.title}</span>
      {c.url && <span className="shrink-0 text-faint">{host(c.url)}</span>}
      {c.locator && <span className="shrink-0 text-faint">· {c.locator}</span>}
    </>
  );
  if (c.url) return <a href={c.url} target="_blank" rel="noreferrer noopener" className="flex min-w-0 items-center gap-1.5 hover:underline">{label}</a>;
  if (c.conversation_id) return <Link to={`/c/${c.conversation_id}`} className="flex min-w-0 items-center gap-1.5 hover:underline">{label}</Link>;
  return <span className="flex min-w-0 items-center gap-1.5">{label}</span>;
}

export function CitationChip({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        className="mx-0.5 inline-flex h-[1.25em] min-w-[1.25em] items-center justify-center rounded-md bg-accent-soft px-1 text-[0.72em] font-semibold text-accent hover:bg-accent hover:text-on-accent"
        aria-label={`Source ${citation.index}: ${citation.title}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      >
        {citation.index}
      </button>
      {open && (
        <span role="dialog" className="absolute bottom-full left-0 z-30 mb-1 block w-72 rounded-lg border border-line bg-surface p-3 text-sm shadow-card">
          <SourceLine c={citation} />
          {citation.snippet && <span className="mt-1.5 block text-xs text-muted line-clamp-4">{citation.snippet}</span>}
        </span>
      )}
    </span>
  );
}

export function SourcesList({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  const implicit = citations.every((c) => c.implicit);
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface-2/50 px-3 py-2 text-sm">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-faint">{implicit ? "Sources consulted" : "Sources"}</div>
      <ol className="space-y-1">
        {citations.map((c) => (
          <li key={c.index} className="flex min-w-0 items-center gap-2">
            <span className="w-5 shrink-0 text-right font-mono text-xs text-faint">{c.index}</span>
            <SourceLine c={c} />
          </li>
        ))}
      </ol>
    </div>
  );
}
