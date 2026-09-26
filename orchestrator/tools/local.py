"""Tools over local data: files, memory, past conversations, artifacts, media re-analysis."""

from __future__ import annotations

from pathlib import Path

from media import video as V
from media.audio import transcribe_cached
from media.ffmpeg import probe
from media.types import Attachment, Kind
from orchestrator.tools.base import Tool, ToolContext, ToolResult, render_source

MAX_READ_CHARS = 30_000


async def _find_attachment(ctx: ToolContext, ref: str) -> dict | None:
    """Resolve an attachment by id or (case-insensitive) filename within this chat / project."""
    ref = ref.strip()
    if ref.startswith("att_"):
        att = await ctx.store.get_attachment(ref)
        if att and (att["conversation_id"] == ctx.conversation_id or await _in_project(ctx, att["id"])):
            return att
        return None
    pool = await ctx.store.conversation_attachments(ctx.conversation_id)
    if ctx.project_id:
        pool += await ctx.store.project_files(ctx.project_id)
    for att in reversed(pool):
        if att["filename"].lower() == ref.lower():
            return att
    for att in reversed(pool):
        if ref.lower() in att["filename"].lower():
            return att
    return None


async def _in_project(ctx: ToolContext, aid: str) -> bool:
    if not ctx.project_id:
        return False
    return any(a["id"] == aid for a in await ctx.store.project_files(ctx.project_id))


async def attachment_text(ctx: ToolContext, att: dict) -> str:
    """Full extracted text of an attachment (document text, transcript, or OCR)."""
    spec = ctx.manager.llm_spec(ctx.model_id)
    prepared = await ctx.router.prepare(
        Attachment(Path(att["path"]), filename=att["filename"], mime=att["mime"], source=att["source"],
                   id=att["id"], sha256=att["sha256"]), spec)
    return "\n\n".join(c.text for c in prepared.context)


class FileRead(Tool):
    name = "file_read"
    description = ("Read the text of a file attached to this chat or in the project knowledge base "
                   "(documents, transcripts of audio/video, OCR of images). Use offset to page through "
                   "long files.")
    parameters = {"type": "object", "properties": {
        "file": {"type": "string", "description": "Attachment id (att_...) or file name"},
        "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Character offset"}},
        "required": ["file"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        att = await _find_attachment(ctx, args["file"])
        if not att:
            return ToolResult(f"No file named {args['file']!r} in this chat or project.", ok=False)
        text = await attachment_text(ctx, att)
        off = int(args.get("offset") or 0)
        chunk = text[off: off + MAX_READ_CHARS]
        more = off + MAX_READ_CHARS < len(text)
        src = ctx.sources.add(att["filename"], attachment_id=att["id"], snippet=chunk[:300])
        note = f"\n… [{len(text) - off - MAX_READ_CHARS} more chars; call again with offset={off + MAX_READ_CHARS}]" if more else ""
        return ToolResult(render_source(src, chunk + note),
                          data={"file": att["filename"], "chars": len(chunk), "total_chars": len(text),
                                "source": src.index})


class FileSearch(Tool):
    name = "file_search"
    description = ("Search inside the files attached to this chat and the project knowledge base. Returns "
                   "the most relevant passages with source ids to cite as [n].")
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6}},
        "required": ["query"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        await ensure_attachments_indexed(ctx)
        scopes = [{"source_type": "attachment", "conversation_id": ctx.conversation_id}]
        if ctx.project_id:
            scopes.append({"source_type": "project_file", "project_id": ctx.project_id})
        hits = []
        for scope in scopes:
            hits += await ctx.retriever.search(args["query"], scope, k=int(args.get("k") or 6))
        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[: int(args.get("k") or 6)]
        if not hits:
            return ToolResult("No matching passages.", data={"query": args["query"], "hits": 0})
        blocks = []
        for h in hits:
            src = ctx.sources.add(h.meta.get("filename", "file"), attachment_id=h.source_id,
                                  locator=h.meta.get("label") or None, snippet=h.text[:300])
            blocks.append(render_source(src, h.text))
        return ToolResult("\n".join(blocks), data={"query": args["query"], "hits": len(hits)})


async def ensure_attachments_indexed(ctx: ToolContext) -> None:
    for att in await ctx.store.conversation_attachments(ctx.conversation_id):
        if att["source"] == "tool" or await ctx.store.has_chunks("attachment", att["id"]):
            continue
        text = await attachment_text(ctx, att)
        if text.strip():
            await ctx.retriever.index("attachment", att["id"], [("", text)],
                                      conversation_id=ctx.conversation_id,
                                      extra_meta={"filename": att["filename"]})


class MemorySave(Tool):
    name = "memory_save"
    description = ("Save a durable fact about the user (preferences, background, ongoing projects) so it "
                   "can be recalled in future chats. Use when the user asks you to remember something or "
                   "shares a lasting personal detail. Write one self-contained sentence.")
    parameters = {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}
    uses_memory = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.memory is None:
            return ToolResult("Memory is turned off for this chat.", ok=False)
        mem = await ctx.memory.save(args["fact"], ctx.conversation_id)
        verb = "Updated" if mem["updated"] else "Saved"
        return ToolResult(f"{verb} memory: {mem['text']}", data={"memory_id": mem["id"], "text": mem["text"],
                                                                "updated": mem["updated"]})


class MemorySearch(Tool):
    name = "memory_search"
    description = "Search saved memories about the user."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    uses_memory = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.memory is None:
            return ToolResult("Memory is turned off for this chat.", ok=False)
        hits = await ctx.memory.search(args["query"], k=10, max_distance=0.8)
        if not hits:
            return ToolResult("No matching memories.", data={"hits": 0})
        return ToolResult("\n".join(f"- {m['text']}" for m in hits), data={"hits": len(hits)})


class ConversationSearch(Tool):
    name = "conversation_search"
    description = ("Search the user's past conversations (other chats) by meaning. Use when the user refers "
                   "to something discussed before. Cite results as [n].")
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    cross_conversation = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        hits = await ctx.retriever.search(args["query"], {"source_type": "message",
                                                          "exclude_conversation_id": ctx.conversation_id}, k=6)
        if not hits:
            return ToolResult("No related past conversations found.", data={"hits": 0})
        blocks = []
        for h in hits:
            conv = await ctx.store.get_conversation(h.conversation_id) if h.conversation_id else None
            title = (conv or {}).get("title") or "Untitled chat"
            src = ctx.sources.add(title, conversation_id=h.conversation_id, snippet=h.text[:300],
                                  locator=h.meta.get("role"))
            blocks.append(render_source(src, h.text))
        return ToolResult("\n".join(blocks), data={"hits": len(hits)})


class CreateArtifact(Tool):
    name = "create_artifact"
    description = (
        "Create or update an artifact shown in the side panel: a standalone HTML page, SVG, React "
        "component (single default-exported component, Tailwind classes allowed), Mermaid diagram, "
        "Markdown document or code file (in Mermaid flowcharts, quote labels that contain punctuation: A[\"f(x) = 1\"]). Reuse the same identifier to publish a new version of an "
        "existing artifact (send the full updated content). Use for substantial, reusable output; keep "
        "short answers in chat. Artifacts run offline: React components may import react, recharts and "
        "lucide-react; HTML pages may load Chart.js, D3 and Tailwind from their usual CDN URLs (served "
        "from local copies); nothing else can be fetched. Browser storage lasts only while it's open.")
    parameters = {"type": "object", "properties": {
        "identifier": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
                       "description": "kebab-case id, stable across versions"},
        "type": {"type": "string", "enum": ["html", "svg", "react", "mermaid", "markdown", "code"]},
        "title": {"type": "string"},
        "language": {"type": "string", "description": "for type=code"},
        "content": {"type": "string"}},
        "required": ["identifier", "type", "title", "content"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        art = await ctx.store.save_artifact_version(ctx.conversation_id, args["identifier"], args["type"],
                                                    args["title"], args["content"], ctx.message_id)
        await ctx.emit("artifact", {**art, "language": args.get("language")})
        return ToolResult(f"Artifact '{art['title']}' saved as version {art['version']} "
                          f"(identifier {art['identifier']}). It is visible to the user in the side panel.",
                          data=art)


class AnalyzeMedia(Tool):
    name = "analyze_media"
    description = ("Look again at part of a video or audio attachment: sample more keyframes from a time "
                   "range and/or re-transcribe that range. Use when the first pass missed detail.")
    parameters = {"type": "object", "properties": {
        "file": {"type": "string", "description": "Attachment id or file name"},
        "start_s": {"type": "number", "minimum": 0},
        "end_s": {"type": "number", "minimum": 0},
        "frames": {"type": "integer", "minimum": 0, "maximum": 16, "default": 8},
        "transcribe": {"type": "boolean", "default": True}},
        "required": ["file", "start_s", "end_s"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        att = await _find_attachment(ctx, args["file"])
        if not att:
            return ToolResult(f"No media file {args['file']!r} in this chat.", ok=False)
        path = Path(att["path"])
        info = await probe(path)
        start = max(0.0, float(args["start_s"]))
        end = min(float(args["end_s"]), info.duration_s or float(args["end_s"]))
        if end <= start:
            return ToolResult(f"Empty range: the file is {info.duration_s:.1f}s long.", ok=False)
        parts, images = [], []
        n = int(args.get("frames", 8)) if info.has_video else 0
        if n:
            step = (end - start) / n
            plan = [(round(start + step * (i + 0.5), 2), "uniform") for i in range(n)]
            spec = ctx.manager.llm_spec(ctx.model_id)
            out_dir = ctx.router.cache.entry_dir(att["sha256"], "zoom", {"s": start, "e": end, "n": n})
            frames = await V.extract_frames(path, plan, out_dir, spec.limits.image_max_side)
            images = [f.path for f in frames]
            parts.append("Keyframes (shown after this result): " + ", ".join(V.fmt_ts(f.t) for f in frames))
        if args.get("transcribe", True) and info.has_audio:
            tr, _ = await transcribe_cached(path, att["sha256"], ctx.manager.stt(), ctx.router.cache,
                                            info.duration_s, start=start, end=end)
            parts.append("Transcript:\n" + (tr.timestamped() or "(no speech)"))
        src = ctx.sources.add(att["filename"], attachment_id=att["id"],
                              locator=f"{V.fmt_ts(start)}–{V.fmt_ts(end)}")
        return ToolResult(render_source(src, "\n\n".join(parts) or "No video or audio in that range."),
                          images=images, data={"file": att["filename"], "start_s": start, "end_s": end,
                                               "frames": len(images), "kind": Kind.VIDEO if n else Kind.AUDIO})
