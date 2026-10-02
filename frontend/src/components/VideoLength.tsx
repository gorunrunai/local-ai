import { TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ModelStatus, VideoModel } from "../api/types";
import { Slider } from "./Slider";

const btn = "inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40";

// Estimated memory and render time grow with clip length (measured per model in config/models.yaml).
export function videoEstimate(m: VideoModel, seconds: number) {
  const extra = Math.max(0, seconds - m.estimate.base_seconds);
  const gb = Math.round((m.estimate.base_gb + m.estimate.gb_per_s * extra) * 10) / 10;
  const renderS = m.estimate.base_render_s == null ? null : m.estimate.base_render_s + (m.estimate.render_s_per_s ?? 0) * extra;
  return { gb, renderS };
}

function duration(s: number) {
  return s < 90 ? `${Math.round(s / 5) * 5 || 5} seconds` : `${Math.round(s / 60)} minutes`;
}

// Settings → Models → Video generation: one model's longest clip, with what it costs on this Mac.
export function VideoLength({ model: m, status, lengths, save }: {
  model: VideoModel; status: ModelStatus; lengths: Record<string, number>;
  save: (lengths: Record<string, number>) => Promise<void>;
}) {
  const chosen = lengths[m.id];
  const [value, setValue] = useState(chosen ?? m.default_max_seconds);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => { setValue(chosen ?? m.default_max_seconds); }, [chosen, m.default_max_seconds]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const store = (v: number | null) => {
    const next = { ...lengths };
    if (v == null || v === m.default_max_seconds) delete next[m.id]; else next[m.id] = v;
    return save(next);
  };
  const change = (v: number) => {   // save once the slider stops moving
    setValue(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => store(v), 400);
  };
  const { gb, renderS } = videoEstimate(m, value);
  const chat = status.llms.find((l) => l.id === status.defaults.llm);
  const unloadsChat = m.unloads_chat_from_seconds != null && value >= m.unloads_chat_from_seconds;
  const untested = m.tested_seconds != null && value > m.tested_seconds;
  const fixed = m.settable_max_seconds <= 1;
  return (
    <div className="space-y-2" data-testid={`video-length-${m.id}`}>
      {!fixed && (
        <Slider label="Longest clip" value={value} min={1} max={m.settable_max_seconds} step={1} onChange={change}
          format={(v) => `${v} seconds${v === m.default_max_seconds ? " (default)" : ""}`} />
      )}
      <p className="text-xs text-muted" data-testid="video-estimate">
        A {value}-second clip uses about {gb} GB of memory while rendering
        {renderS != null && <> and takes about {duration(renderS)} to render on this Mac</>}.
        {m.settable_max_seconds < m.ceiling_seconds && <> Limited to {m.settable_max_seconds} seconds by this Mac's memory ({status.budget_gb} GB for AI models).</>}
        {m.ceiling_seconds <= m.default_max_seconds && <> This model can't make clips longer than {m.default_max_seconds} seconds.</>}
      </p>
      {untested && (
        <Warning testid="video-warn-untested">
          <b>Longer than tested.</b> Clips have been checked up to {m.tested_seconds} seconds. Longer ones may drift, repeat,
          or lose a person's likeness, especially when animating a photo. Try one before relying on it.
        </Warning>
      )}
      {unloadsChat && (
        <Warning testid="video-warn-memory">
          <b>Uses most of the memory.</b> Together with the chat model{chat ? ` (${chat.display_name})` : ""} this is more than
          the {status.budget_gb} GB set aside for AI models, so the chat model is unloaded while the clip renders. It loads
          again afterwards, so your next reply takes longer to start.
        </Warning>
      )}
      {renderS != null && renderS > 180 && (
        <Warning testid="video-warn-time">
          <b>Long render.</b> Your Mac works hard for the whole time: it may get warm and its fans may run, and other apps can
          feel slow. You can stop a render from the chat.
        </Warning>
      )}
      {chosen != null && chosen !== m.default_max_seconds && (
        <button type="button" className={btn} onClick={() => store(null)}>Reset to {m.default_max_seconds} seconds</button>
      )}
    </div>
  );
}

function Warning({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <div className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn" data-testid={testid}>
      <TriangleAlert size={14} className="mt-0.5 shrink-0" />
      <p>{children}</p>
    </div>
  );
}
