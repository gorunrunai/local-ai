import { useEffect, useState } from "react";
import { get } from "../api/client";
import { Modal } from "./Modal";

interface Debug {
  model: string;
  context_tokens: number;
  reserved_output: number;
  tools_tokens: number;
  system_tokens: number;
  total_prompt_tokens: number;
  stages: { name: string; tokens: number; [k: string]: unknown }[];
  tools: string[];
  messages: { role: string; content: unknown; tool_calls?: unknown }[];
  sources?: { index: number; title: string }[];
}

function renderContent(c: unknown): string {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c.map((p) => {
      const part = p as { type: string; text?: string; path?: string };
      if (part.type === "text") return part.text ?? "";
      if (part.type === "image_url") return "[image]";
      return `[${part.type}${part.path ? `: ${part.path}` : ""}]`;
    }).join("\n");
  }
  return JSON.stringify(c, null, 2);
}

/** "What exactly did the model see?" — the final prompt, stage token counts and tools. */
export function PromptDebugModal({ messageId, onClose }: { messageId: string; onClose: () => void }) {
  const [d, setD] = useState<Debug | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get<Debug>(`/messages/${messageId}/prompt`).then(setD, (e) => setErr(String(e.message ?? e)));
  }, [messageId]);
  return (
    <Modal title="Prompt sent to the model" onClose={onClose} wide>
      {err && <p className="text-sm text-danger">{err}</p>}
      {d && (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-muted">
            <span>Model <b className="text-fg">{d.model}</b></span>
            <span>Prompt <b className="text-fg tabular-nums">{d.total_prompt_tokens.toLocaleString()}</b> / {d.context_tokens.toLocaleString()} tokens</span>
            <span>Tools <b className="text-fg tabular-nums">{d.tools_tokens.toLocaleString()}</b></span>
            <span>Reserved for reply <b className="text-fg tabular-nums">{d.reserved_output.toLocaleString()}</b></span>
          </div>
          <table className="w-full text-left text-xs">
            <thead className="text-faint"><tr><th className="py-1 font-medium">Stage</th><th className="font-medium">Tokens</th><th className="font-medium">Details</th></tr></thead>
            <tbody>
              {d.stages.map((s) => (
                <tr key={s.name} className="border-t border-line">
                  <td className="py-1 font-mono">{s.name}</td>
                  <td className="tabular-nums">{s.tokens}</td>
                  <td className="text-muted">{Object.entries(s).filter(([k]) => !["name", "tokens", "preview"].includes(k)).map(([k, v]) => `${k}: ${v}`).join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.tools.length > 0 && <div className="text-xs text-muted">Tools offered: <span className="font-mono">{d.tools.join(", ")}</span></div>}
          <div className="space-y-2">
            {d.messages.map((m, i) => (
              <details key={i} open={i === 0 || i === d.messages.length - 1} className="rounded-lg border border-line">
                <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted">{m.role}{m.tool_calls ? " · tool calls" : ""}</summary>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap border-t border-line p-3 font-mono text-xs">{renderContent(m.content)}{m.tool_calls ? `\n\n${JSON.stringify(m.tool_calls, null, 2)}` : ""}</pre>
              </details>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
