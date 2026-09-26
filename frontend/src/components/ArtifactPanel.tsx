import clsx from "clsx";
import { Check, Code2, Copy, Download, ExternalLink, Eye, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiUrl, get } from "../api/client";
import type { Artifact } from "../api/types";
import { useUi } from "../store/ui";
import { Markdown, useCopy } from "./Markdown";

interface Version {
  version: number;
  content: string;
  created_at: number;
}
interface Full extends Artifact {
  versions: Version[];
}

const EXT: Record<string, string> = { html: "html", svg: "svg", react: "jsx", mermaid: "mmd", markdown: "md" };
const FRAMED = new Set(["html", "svg", "react"]);

export function viewUrl(id: string, version: number) {
  return apiUrl(`/artifacts/${id}/view?version=${version}`);
}

/** Side panel: sandboxed preview, source, version history, download / open in a new tab. */
export function ArtifactPanel() {
  const { artifact, closeArtifact, artifactWidth, setArtifactWidth } = useUi();
  const [full, setFull] = useState<Full | null>(null);
  const [missing, setMissing] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [copied, copy] = useCopy();
  const dragging = useRef(false);

  useEffect(() => {
    if (!artifact) return;
    let cancelled = false;
    setMissing(false);
    get<Full>(`/artifacts/${artifact.id}`).then((f) => {
      if (cancelled) return;
      setFull(f);
      setVersion(artifact.version ?? f.versions[f.versions.length - 1]?.version ?? 1);
    }).catch(() => { if (!cancelled) setMissing(true); });           // e.g. cleared in Settings → Clear clutter
    return () => {
      cancelled = true;
    };
  }, [artifact?.id, artifact?.version, artifact?.nonce]);

  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!dragging.current) return;
      setArtifactWidth(Math.min(Math.max(window.innerWidth - e.clientX, 340), window.innerWidth - 380));
    };
    const up = () => {
      dragging.current = false;
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [setArtifactWidth]);

  if (!artifact) return null;
  const type = full?.type ?? artifact.type;
  const v = full?.versions.find((x) => x.version === version);
  const content = v?.content ?? "";
  const lang = type === "code" ? (artifact.language ?? "text") : type === "react" ? "jsx" : type === "mermaid" ? "mermaid" : type;
  const canPreview = type !== "code";

  const download = () => {
    const ext = type === "code" ? (artifact.language ?? "txt") : EXT[type] ?? "txt";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
    a.download = `${full?.identifier ?? "artifact"}-v${version}.${ext}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <aside aria-label={`Artifact: ${full?.title ?? artifact.title}`} data-testid="artifact-panel"
      className="no-print relative flex h-full shrink-0 flex-col border-l border-line bg-surface max-md:fixed max-md:inset-0 max-md:z-40 max-md:!w-full"
      style={{ width: artifactWidth }}>
      <div role="separator" aria-orientation="vertical" aria-label="Resize artifact panel"
        onPointerDown={() => { dragging.current = true; document.body.style.userSelect = "none"; }}
        className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize hover:bg-accent/30 max-md:hidden" />
      <header className="flex items-center gap-2 border-b border-line px-3 py-2" style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{full?.title ?? artifact.title}</div>
          <div className="text-xs text-faint">{type.toUpperCase()}{artifact.language ? ` · ${artifact.language}` : ""}</div>
        </div>
        {full && full.versions.length > 1 && (
          <select aria-label="Version" value={version ?? ""} onChange={(e) => setVersion(Number(e.target.value))}
            className="rounded-md border border-line bg-surface px-2 py-1 text-xs">
            {full.versions.map((x) => (
              <option key={x.version} value={x.version}>v{x.version} · {new Date(x.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</option>
            ))}
          </select>
        )}
        {canPreview && (
          <div role="tablist" className="flex rounded-md bg-surface-2 p-0.5">
            <button role="tab" type="button" aria-selected={tab === "preview"} onClick={() => setTab("preview")} title="Preview"
              className={clsx("rounded p-1", tab === "preview" ? "bg-surface text-fg shadow-sm" : "text-muted")}><Eye size={15} /></button>
            <button role="tab" type="button" aria-selected={tab === "code"} onClick={() => setTab("code")} title="Code"
              className={clsx("rounded p-1", tab === "code" ? "bg-surface text-fg shadow-sm" : "text-muted")}><Code2 size={15} /></button>
          </div>
        )}
        <button type="button" onClick={() => copy(content)} aria-label="Copy source" title="Copy" className="rounded-md p-1.5 text-muted hover:bg-surface-2">{copied ? <Check size={15} /> : <Copy size={15} />}</button>
        <button type="button" onClick={download} aria-label="Download" title="Download" className="rounded-md p-1.5 text-muted hover:bg-surface-2"><Download size={15} /></button>
        {version != null && (
          <a href={viewUrl(artifact.id, version)} target="_blank" rel="noreferrer" aria-label="Open in new tab" title="Open in new tab"
            className="rounded-md p-1.5 text-muted hover:bg-surface-2"><ExternalLink size={15} /></a>
        )}
        <button type="button" onClick={closeArtifact} aria-label="Close artifact" className="rounded-md p-1.5 text-muted hover:bg-surface-2"><X size={16} /></button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {missing ? (
          <p className="p-6 text-center text-sm text-muted" data-testid="artifact-cleared">This was cleared to free up space (Settings → Clear clutter). Ask again to make a new one.</p>
        ) : !full || version == null ? null : tab === "preview" && canPreview ? (
          FRAMED.has(type) ? (
            <iframe key={`${artifact.id}-${version}`} title={full.title} src={viewUrl(artifact.id, version)}
              sandbox="allow-scripts allow-modals allow-forms allow-downloads" className="h-full w-full border-0 bg-white" />
          ) : (
            <div className="p-4"><Markdown text={type === "mermaid" ? "```mermaid\n" + content + "\n```" : content} /></div>
          )
        ) : (
          <div className="p-3"><Markdown text={"```" + lang + "\n" + content + "\n```"} /></div>
        )}
      </div>
    </aside>
  );
}
