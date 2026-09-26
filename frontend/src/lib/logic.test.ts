import remarkParse from "remark-parse";
import { unified } from "unified";
import { describe, expect, it } from "vitest";
import { parseFrame, parseSSE } from "../api/client";
import type { Message, StreamEvent } from "../api/types";
import { remarkCitations } from "./citations";
import { applyEvent, blocksToView, emptyLive } from "./live";
import { buildPath, recencyGroup } from "./tree";

function msg(id: string, parent: string | null, role: "user" | "assistant", t: number): Message {
  return { id, conversation_id: "c", parent_id: parent, role, content: id, blocks: [], citations: [], model: null,
    status: "complete", pinned: 0, usage: null, created_at: t, attachment_ids: [] };
}

describe("SSE parsing", () => {
  it("parses frames split across chunks, ignoring pings", async () => {
    const enc = new TextEncoder();
    const chunks = [': ping\n\nevent: text_delta\ndata: {"te', 'xt":"Hel"}\n\nevent: text_delta\r\ndata: {"text":"lo"}\r\n\r\n', 'event: message_end\ndata: {"status":"complete"}\n\n'];
    const stream = new ReadableStream<Uint8Array>({ start(c) { chunks.forEach((x) => c.enqueue(enc.encode(x))); c.close(); } });
    const out: StreamEvent[] = [];
    for await (const ev of parseSSE(stream)) out.push(ev);
    expect(out.map((e) => e.event)).toEqual(["text_delta", "text_delta", "message_end"]);
    expect((out[0] as { data: { text: string } }).data.text).toBe("Hel");
  });
  it("ignores malformed data", () => {
    expect(parseFrame("event: x\ndata: {nope")).toBeNull();
  });
});

describe("live reply reducer", () => {
  const run = (events: StreamEvent[]) => events.reduce(applyEvent, emptyLive());
  it("interleaves thinking, text and tool cards", () => {
    const s = run([
      { event: "message_start", data: { conversation_id: "c", user_message: { id: "u" }, assistant_message_id: "a", model: "m", incognito: false } },
      { event: "thinking_delta", data: { text: "hmm " } },
      { event: "thinking_delta", data: { text: "ok" } },
      { event: "text_delta", data: { text: "Let me run code." } },
      { event: "tool_call", data: { id: "t1", name: "code_exec", arguments: "{}", status: "pending" } },
      { event: "tool_confirmation", data: { id: "t1", name: "code_exec", arguments: { code: "1" }, reason: "approval" } },
      { event: "tool_result", data: { id: "t1", name: "code_exec", ok: true, status: "ok", decision: "confirm-approved", duration_ms: 12, preview: "1", data: {}, files: [] } },
      { event: "text_delta", data: { text: "Done." } },
      { event: "citation", data: { index: 1, title: "Page" } },
      { event: "citation", data: { index: 1, title: "Page" } },
      { event: "message_end", data: { message_id: "a", status: "complete", finish_reason: "stop" } },
    ]);
    expect(s.blocks.map((b) => b.kind)).toEqual(["thinking", "text", "tool", "text"]);
    expect(s.blocks[0]).toMatchObject({ text: "hmm ok", done: true });
    expect(s.blocks[2]).toMatchObject({ tool: { status: "ok", decision: "confirm-approved", duration_ms: 12 } });
    expect(s.citations).toHaveLength(1);
    expect(s.done).toBe(true);
  });
  it("marks tool as awaiting confirmation until the result arrives", () => {
    const s = run([
      { event: "tool_call", data: { id: "t", name: "x", arguments: {}, status: "pending" } },
      { event: "tool_confirmation", data: { id: "t", name: "x", arguments: {}, reason: "side effects" } },
    ]);
    expect(s.blocks[0]).toMatchObject({ kind: "tool", tool: { status: "awaiting_confirmation", reason: "side effects" } });
  });
  it("tracks render progress on a running tool and clears it with the result", () => {
    const running = run([
      { event: "tool_call", data: { id: "v", name: "generate_video", arguments: { prompt: "fox" }, status: "running" } },
      { event: "tool_progress", data: { id: "v", name: "generate_video", stage: "Loading DiT" } },
      { event: "tool_progress", data: { id: "v", name: "generate_video", stage: "Denoising", step: 3, total: 8, elapsed_s: 41 } },
    ]);
    expect(running.blocks[0]).toMatchObject({ tool: { status: "running", progress: { stage: "Denoising", step: 3, total: 8 } } });
    const done = applyEvent(running, { event: "tool_result", data: { id: "v", name: "generate_video", ok: true, status: "ok",
      decision: "confirm-approved", duration_ms: 90000, preview: "", data: { seconds: 4 }, files: [{ id: "att_1", filename: "ltx.mp4", size: 1 }] } });
    expect(done.blocks[0]).toMatchObject({ tool: { status: "ok", files: [{ filename: "ltx.mp4" }] } });
    expect((done.blocks[0] as { tool: { progress?: unknown } }).tool.progress).toBeUndefined();
  });
  it("does not mutate the previous state", () => {
    const a = applyEvent(emptyLive(), { event: "text_delta", data: { text: "a" } });
    const b = applyEvent(a, { event: "text_delta", data: { text: "b" } });
    expect(a.blocks[0]).toMatchObject({ text: "a" });
    expect(b.blocks[0]).toMatchObject({ text: "ab" });
  });
  it("rebuilds stored blocks into the same view", () => {
    const view = blocksToView([
      { type: "text", text: "pre" },
      { type: "tool_use", id: "t", name: "web_fetch", arguments: { url: "u" }, decision: "allow" },
      { type: "tool_result", id: "t", name: "web_fetch", ok: false, status: "denied", content: "no", data: {}, files: [], duration_ms: 3 },
    ], "pre");
    expect(view.map((b) => b.kind)).toEqual(["text", "tool"]);
    expect(view[1]).toMatchObject({ tool: { status: "denied", preview: "no" } });
    expect(blocksToView([], "plain")).toEqual([{ kind: "text", text: "plain" }]);
  });
});

describe("branch path", () => {
  it("finds sibling versions for edited user messages and regenerated replies", () => {
    const ms = [msg("u1", null, "user", 1), msg("a1", "u1", "assistant", 2), msg("a1b", "u1", "assistant", 5),
      msg("u1b", null, "user", 3), msg("a2", "u1b", "assistant", 4)];
    const path = buildPath(ms, ["u1", "a1b"]);
    expect(path.map((p) => [p.message.id, p.index, p.siblings.length])).toEqual([["u1", 0, 2], ["a1b", 1, 2]]);
  });
  it("groups by recency", () => {
    const now = Date.now() / 1000;
    expect(recencyGroup(now)).toBe("Today");
    expect(recencyGroup(now - 3 * 86400)).toBe("Previous 7 days");
    expect(recencyGroup(now - 400 * 86400)).toBe("Older");
  });
});

describe("citation markers", () => {
  const cite = (md: string, known: number[]) => {
    const tree = unified().use(remarkParse).parse(md);
    remarkCitations(new Set(known))()(tree as never);
    return JSON.stringify(tree);
  };
  it("links known source numbers in prose", () => {
    const t = cite("Water boils at 100 °C [1] and freezes at 0 [1, 2].", [1, 2]);
    expect(t).toContain('"url":"#cite-1"');
    expect(t).toContain('"url":"#cite-2"');
  });
  it("leaves unknown numbers, code and array indexing alone", () => {
    const t = cite("See [7]. Use `arr[1]` and\n\n```\nx[2]\n```", [1, 2]);
    expect(t).not.toContain("#cite-");
  });
});
