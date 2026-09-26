import clsx from "clsx";
import { AudioLines, FileText, Film, Image as ImageIcon, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { fileUrl } from "../api/client";
import type { Attachment } from "../api/types";

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function fmtDuration(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export function kindOf(a: { kind?: string | null; mime?: string | null; filename: string }): "image" | "video" | "audio" | "doc" {
  const k = a.kind ?? "";
  if (k === "image" || k === "screenshot" || a.mime?.startsWith("image/")) return "image";
  if (k === "video" || a.mime?.startsWith("video/")) return "video";
  if (k === "audio" || a.mime?.startsWith("audio/")) return "audio";
  return "doc";
}

/** Attachment as shown inside a sent user message. */
export function SentAttachment({ att, progress }: { att: Attachment; progress?: { fraction: number; message: string } }) {
  const kind = kindOf(att);
  const busy = progress && progress.fraction < 1;
  // Settings → Clear clutter empties the file but keeps the record (size 0), so say so instead of breaking.
  const [gone, setGone] = useState(att.size === 0 && !busy);
  if (gone) {
    return (
      <figure className="m-0 max-w-64 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-muted" data-testid="cleared-upload">
        <span className="block truncate font-medium text-fg">{att.source === "voice_message" ? "Voice message" : att.filename}</span>
        Cleared to free up space (Settings → Clear clutter).
      </figure>
    );
  }
  return (
    <figure className="relative m-0">
      {kind === "image" && (
        <a href={fileUrl(att.id)} target="_blank" rel="noreferrer">
          <img src={fileUrl(att.id)} alt={att.filename} onError={() => setGone(true)} className="max-h-60 max-w-full rounded-lg border border-line object-contain" />
        </a>
      )}
      {kind === "video" && (
        <video src={fileUrl(att.id)} controls preload="metadata" onError={() => setGone(true)} className="max-h-60 max-w-full rounded-lg border border-line bg-black" aria-label={att.filename} />
      )}
      {kind === "audio" && (
        <div className="rounded-lg border border-line bg-surface p-2">
          <div className="mb-1 flex items-center gap-1.5 text-xs text-muted">
            <AudioLines size={13} /> {att.source === "voice_message" ? "Voice message" : att.filename}
          </div>
          <audio src={fileUrl(att.id)} controls preload="metadata" onError={() => setGone(true)} className="h-9 w-64 max-w-full" />
          {att.voice_note && <div className="mt-1.5 max-w-72 text-xs text-muted">{att.voice_note.replace(/^Gist: .*\n?/, "").replace(/^Delivery:\s*/, "Tone: ")}</div>}
        </div>
      )}
      {kind === "doc" && (
        <a href={fileUrl(att.id)} target="_blank" rel="noreferrer"
          className="flex max-w-64 items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm hover:bg-surface-2">
          <FileText size={18} className="shrink-0 text-muted" />
          <span className="min-w-0">
            <span className="block truncate font-medium">{att.filename}</span>
            <span className="text-xs text-faint">{fmtBytes(att.size)}</span>
          </span>
        </a>
      )}
      {busy && (
        <figcaption className="mt-1 flex items-center gap-1.5 text-xs text-muted" aria-live="polite">
          <Loader2 size={12} className="animate-spin" /> {progress.message || "Processing"} · {Math.round(progress.fraction * 100)}%
        </figcaption>
      )}
    </figure>
  );
}

export interface DraftAttachment {
  key: string;
  file: File;
  source: string;
  progress: number;         // upload 0..1
  uploaded?: Attachment;
  error?: string;
  previewUrl?: string;
  duration?: number;        // seconds, for audio/video
}

/** Attachment chip in the composer before sending (thumbnail, video first frame + duration). */
export function DraftChip({ d, onRemove }: { d: DraftAttachment; onRemove: () => void }) {
  const kind = kindOf({ mime: d.file.type, filename: d.file.name, kind: d.source === "screenshot" ? "screenshot" : null });
  const [duration, setDuration] = useState<number | undefined>(d.duration);
  useEffect(() => setDuration(d.duration), [d.duration]);
  const Icon = kind === "image" ? ImageIcon : kind === "video" ? Film : kind === "audio" ? AudioLines : FileText;
  return (
    <div className={clsx("group relative flex h-16 min-w-0 items-center gap-2 overflow-hidden rounded-lg border bg-surface pr-7", d.error ? "border-danger" : "border-line")}>
      {kind === "image" && d.previewUrl ? (
        <img src={d.previewUrl} alt="" className="h-16 w-16 shrink-0 object-cover" />
      ) : kind === "video" && d.previewUrl ? (
        <video src={`${d.previewUrl}#t=0.1`} muted preload="metadata" className="h-16 w-20 shrink-0 bg-black object-cover"
          onLoadedMetadata={(e) => setDuration((e.target as HTMLVideoElement).duration)} />
      ) : (
        <span className="ml-2 grid h-10 w-10 shrink-0 place-items-center rounded-md bg-surface-2"><Icon size={18} className="text-muted" /></span>
      )}
      <span className="min-w-0 max-w-40 py-1">
        <span className="block truncate text-xs font-medium">{d.source === "voice_message" ? "Voice message" : d.file.name}</span>
        <span className="block text-[11px] text-faint">
          {d.error ? <span className="text-danger">{d.error}</span>
            : d.uploaded ? <>{fmtBytes(d.file.size)}{duration ? ` · ${fmtDuration(duration)}` : ""}</>
            : `Uploading ${Math.round(d.progress * 100)}%`}
        </span>
      </span>
      {!d.uploaded && !d.error && <span className="absolute bottom-0 left-0 h-0.5 bg-accent" style={{ width: `${d.progress * 100}%` }} />}
      <button type="button" onClick={onRemove} aria-label={`Remove ${d.file.name}`}
        className="absolute top-1 right-1 rounded-full bg-surface-3 p-0.5 text-muted opacity-80 hover:text-fg group-hover:opacity-100">
        <X size={12} />
      </button>
    </div>
  );
}
