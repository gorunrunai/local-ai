import type { Artifact, Block, Citation, FileRef, StreamEvent, Usage } from "../api/types";

/** A tool call as the UI shows it while (and after) it runs. */
export interface ToolView {
  id: string;
  name: string;
  arguments: unknown;
  status: "pending" | "running" | "awaiting_confirmation" | "ok" | "error" | "denied";
  reason?: string;
  decision?: string;
  preview?: string;
  data?: Record<string, unknown>;
  files?: FileRef[];
  duration_ms?: number;
  progress?: { stage: string; step?: number; total?: number; elapsed_s?: number };
}

export type LiveBlock =
  | { kind: "thinking"; text: string; done: boolean }
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: ToolView };

export interface LiveReply {
  conversationId: string | null;
  userMessageId: string | null;
  assistantId: string | null;
  model: string | null;
  blocks: LiveBlock[];
  citations: Citation[];
  artifacts: Artifact[];
  status: { stage: string; message: string } | null;
  media: Record<string, { stage: string; fraction: number; message: string }>;
  warnings: Record<string, string[]>;
  usage: Usage | null;
  error: string | null;
  done: boolean;
}

export const emptyLive = (): LiveReply => ({
  conversationId: null, userMessageId: null, assistantId: null, model: null, blocks: [], citations: [],
  artifacts: [], status: null, media: {}, warnings: {}, usage: null, error: null, done: false,
});

function closeThinking(blocks: LiveBlock[]) {
  const last = blocks[blocks.length - 1];
  if (last?.kind === "thinking") last.done = true;
}

/** Pure reducer: apply one SSE event to the live reply (returns a new object). */
export function applyEvent(prev: LiveReply, ev: StreamEvent): LiveReply {
  const s: LiveReply = { ...prev, blocks: prev.blocks.map((b) => (b.kind === "tool" ? { ...b, tool: { ...b.tool } } : { ...b })) };
  const last = s.blocks[s.blocks.length - 1];
  switch (ev.event) {
    case "message_start":
      s.conversationId = ev.data.conversation_id;
      s.userMessageId = ev.data.user_message.id;
      s.assistantId = ev.data.assistant_message_id;
      s.model = ev.data.model;
      break;
    case "status":
      s.status = ev.data;
      break;
    case "media_progress":
      s.media = { ...s.media, [ev.data.attachment_id]: ev.data };
      break;
    case "media_warning":
      s.warnings = { ...s.warnings, [ev.data.attachment_id]: ev.data.warnings };
      break;
    case "prompt_ready":
      s.status = null;
      break;
    case "thinking_delta":
      s.status = null;
      if (last?.kind === "thinking" && !last.done) last.text += ev.data.text;
      else s.blocks.push({ kind: "thinking", text: ev.data.text, done: false });
      break;
    case "text_delta":
      s.status = null;
      closeThinking(s.blocks);
      if (last?.kind === "text") last.text += ev.data.text;
      else s.blocks.push({ kind: "text", text: ev.data.text });
      break;
    case "text_replace": {
      // Rare: a tool call written as text was removed after streaming. Keep tool cards, rebuild text.
      s.blocks = s.blocks.filter((b) => b.kind !== "text");
      s.blocks.push({ kind: "text", text: ev.data.text });
      break;
    }
    case "tool_call": {
      closeThinking(s.blocks);
      const existing = s.blocks.find((b) => b.kind === "tool" && b.tool.id === ev.data.id);
      if (existing && existing.kind === "tool") {
        existing.tool.status = ev.data.status;
        existing.tool.arguments = ev.data.arguments;
      } else {
        s.blocks.push({ kind: "tool", tool: { id: ev.data.id, name: ev.data.name, arguments: ev.data.arguments, status: ev.data.status } });
      }
      break;
    }
    case "tool_confirmation": {
      const t = s.blocks.find((b) => b.kind === "tool" && b.tool.id === ev.data.id);
      if (t && t.kind === "tool") {
        t.tool.status = "awaiting_confirmation";
        t.tool.reason = ev.data.reason;
        t.tool.arguments = ev.data.arguments;
      }
      break;
    }
    case "tool_progress": {
      const t = s.blocks.find((b) => b.kind === "tool" && b.tool.id === ev.data.id);
      if (t && t.kind === "tool") t.tool.progress = { stage: ev.data.stage, step: ev.data.step, total: ev.data.total, elapsed_s: ev.data.elapsed_s };
      break;
    }
    case "tool_result": {
      const t = s.blocks.find((b) => b.kind === "tool" && b.tool.id === ev.data.id);
      if (t && t.kind === "tool") {
        t.tool.status = ev.data.status === "ok" ? "ok" : ev.data.status === "denied" ? "denied" : "error";
        Object.assign(t.tool, {
          decision: ev.data.decision, preview: ev.data.preview, data: ev.data.data, files: ev.data.files,
          duration_ms: ev.data.duration_ms, progress: undefined,
        });
      }
      break;
    }
    case "citation":
      if (!s.citations.some((c) => c.index === ev.data.index)) s.citations = [...s.citations, ev.data];
      break;
    case "artifact":
      s.artifacts = [...s.artifacts.filter((a) => !(a.id === ev.data.id && a.version === ev.data.version)), ev.data];
      break;
    case "usage":
      s.usage = ev.data;
      break;
    case "error":
      s.error = ev.data.message;
      s.status = null;
      break;
    case "message_end":
      closeThinking(s.blocks);
      s.done = true;
      s.status = null;
      break;
  }
  return s;
}

/** Convert stored blocks (from the DB) into the same view model the live stream uses. */
export function blocksToView(blocks: Block[], content: string): LiveBlock[] {
  const out: LiveBlock[] = [];
  const tools = new Map<string, ToolView>();
  for (const b of blocks) {
    if (b.type === "thinking") out.push({ kind: "thinking", text: b.text, done: true });
    else if (b.type === "text") out.push({ kind: "text", text: b.text });
    else if (b.type === "tool_use") {
      const tool: ToolView = { id: b.id, name: b.name, arguments: b.arguments, status: "running", decision: b.decision };
      tools.set(b.id, tool);
      out.push({ kind: "tool", tool });
    } else if (b.type === "tool_result") {
      const tool = tools.get(b.id);
      if (tool) {
        tool.status = b.status === "ok" ? "ok" : b.status === "denied" ? "denied" : "error";
        Object.assign(tool, { preview: b.content, data: b.data, files: b.files, duration_ms: b.duration_ms });
      }
    }
  }
  if (!out.some((b) => b.kind === "text") && content) out.push({ kind: "text", text: content });
  return out;
}
