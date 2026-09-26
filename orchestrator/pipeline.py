"""Prompt enrichment: ordered stages, each with a token budget, producing the final messages.

Layout, chosen so the model server's prefix cache survives from turn to turn:

    [system]  base rules · style · custom instructions · project instructions + small project
              knowledge · summary of older history                 ← changes rarely
    [history] earlier turns (media degraded by age, tool turns included)
    [user]    <turn_context> memories · retrieved passages · file list </turn_context>
              current attachments (media parts + data envelopes) · the user's text   ← per turn
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from inference.config import ROOT, LLMSpec
from inference.types import ChatRequest, TextDelta
from media.compose import DATA_ENVELOPE_RULES
from media.types import Attachment, ContextBlock, Kind, PreparedAttachment
from orchestrator.rag import MemoryService, Retriever
from orchestrator.storage.store import Store
from orchestrator.tokens import TokenCounter

log = logging.getLogger(__name__)
PROMPTS = ROOT / "config" / "prompts"
STYLES = ROOT / "config" / "styles"

VOICE_RULES = (
    "## Voice conversation\nThe user is talking to you out loud and hears your reply through text-to-speech. "
    "Answer the way a person would in conversation: start with the answer in a short first sentence, keep "
    "replies to a few sentences unless asked for more, and write plain spoken prose: no Markdown, lists, "
    "tables, emoji or URLs. Spell out symbols and units. The user's words come from speech recognition and "
    "may contain small transcription errors; interpret them sensibly.\n\n"
    "You have the same tools as in typed chat, and what they make appears on the user's screen during the "
    "conversation. To show a diagram, chart, table, code or page, call create_artifact; to make a video, "
    "call generate_video; to look something up, call web_search. If a tool needs the user's approval, "
    "Allow and Deny buttons appear on their screen: say so briefly. Only say you made, found, searched, "
    "saved or showed something after the tool call for it has succeeded in this reply, and never describe "
    "results you haven't received. Tools finish inside your reply: nothing keeps running afterwards and you "
    "can't come back later on your own, so never say something is still rendering or promise to let the "
    "user know. If they ask whether something is ready and no tool result shows it was made, it was never "
    "started: start it now. If a tool fails or is declined, say that plainly. Don't narrate each "
    "step; one short sentence such as \"Let me make that\" is enough.")

BUDGETS = {"system": 4000, "project_inline": 6000, "memory": 800, "retrieval": 4000, "project_chunks": 4000}
KEEP_IMAGES_TURNS = 2       # user turns back for which images are re-sent
KEEP_DOCS_TURNS = 1         # older documents become stubs (retrieval covers them)
SAFETY_TOKENS = 256


@dataclass
class TurnInput:
    conversation: dict
    history: list[dict]                 # messages on the path before the current user message
    user_message: dict
    prepared: list[PreparedAttachment]  # current turn's attachments, already routed
    attachments: list[dict]             # DB rows for current attachments (same order)
    spec: LLMSpec
    prefs: dict
    incognito: bool
    project: dict | None
    tools: list[dict]
    thinking: bool = False
    voice: bool = False


@dataclass
class BuiltPrompt:
    messages: list[dict]
    debug: dict
    sources_used: list[dict] = field(default_factory=list)


def load_style(name: str | None, custom: dict) -> str:
    if not name or name == "default":
        return ""
    if name in custom:
        return custom[name]
    p = STYLES / f"{Path(name).name}.md"
    return p.read_text().strip() if p.exists() else ""


class PromptBuilder:
    def __init__(self, store: Store, retriever: Retriever, memory: MemoryService | None, router, manager,
                 sources, emit):
        self.store = store
        self.retriever = retriever
        self.memory = memory
        self.router = router
        self.manager = manager
        self.sources = sources
        self.emit = emit

    # --- entry point ---------------------------------------------------------------------
    async def build(self, t: TurnInput) -> BuiltPrompt:
        counter = TokenCounter(t.spec)
        stages: list[dict] = []
        query = t.user_message["content"] or " ".join(a["filename"] for a in t.attachments)

        system = await self._system(t, stages, counter)
        turn_ctx = await self._turn_context(t, query, stages, counter)
        user_msg = self._current_user(t, turn_ctx)
        cur_tokens = counter.message(user_msg)
        stages.append({"name": "current_turn", "tokens": cur_tokens,
                       "preview": (t.user_message["content"] or "")[:200]})

        ctx_len = min(t.prefs.get("context_tokens") or t.spec.context_length, t.spec.context_length)
        reserve = int(t.prefs.get("max_tokens") or 4096) + (t.spec.thinking_budget or 1024 if t.thinking else 0)
        tools_tokens = counter.text(json.dumps(t.tools)) if t.tools else 0
        sys_tokens = counter.text(system)
        history_budget = ctx_len - reserve - tools_tokens - sys_tokens - cur_tokens - SAFETY_TOKENS

        history, summary, h_meta = await self._history(t, history_budget, counter)
        if summary:
            system += f"\n\n## Earlier in this conversation (summary)\n{summary}"
            sys_tokens = counter.text(system)
        stages.append({"name": "history", "tokens": h_meta["tokens"], "messages": h_meta["kept"],
                       "summarized": h_meta["summarized"], "budget": history_budget})

        messages = [{"role": "system", "content": system}, *history, user_msg]
        total = counter.messages(messages) + tools_tokens
        debug = {
            "model": t.spec.id, "context_tokens": ctx_len, "reserved_output": reserve,
            "tools_tokens": tools_tokens, "system_tokens": sys_tokens, "total_prompt_tokens": total,
            "stages": stages, "tools": [x["function"]["name"] for x in t.tools],
            "messages": _sanitize(messages),
        }
        return BuiltPrompt(messages=messages, debug=debug)

    # --- stage: system prompt (stable) --------------------------------------------------
    async def _system(self, t: TurnInput, stages: list, counter: TokenCounter) -> str:
        tz_name = t.prefs.get("timezone")
        try:
            tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
        except Exception:  # noqa: BLE001
            tz = datetime.now().astimezone().tzinfo
        now = datetime.now(tz)
        # Hour resolution keeps the system prompt identical across turns (prefix cache).
        base = (PROMPTS / "system.md").read_text().format(
            datetime=now.strftime("%A %d %B %Y, around %H:00"), timezone=str(tz),
            locale=t.prefs.get("locale", "en-US"), data_rules=DATA_ENVELOPE_RULES)
        parts = [base]
        style = load_style(t.conversation.get("style") or t.prefs.get("style"),
                           t.prefs.get("custom_styles", {}))
        if style:
            parts.append(f"## Response style\n{style}")
        if ci := (t.prefs.get("custom_instructions") or "").strip():
            parts.append(f"## The user's standing instructions\n{ci}")
        if t.incognito:
            parts.append("## Incognito\nThis chat is incognito: it will not be saved, and saved memories "
                         "are neither read nor written. If the user asks you to remember something, tell them "
                         "memory is off in incognito chats; never claim to have saved it.")
        stages.append({"name": "system", "tokens": counter.text("\n\n".join(parts))})
        if t.project:
            proj = await self._project_inline(t, counter)
            if proj:
                parts.append(proj)
                stages.append({"name": "project", "tokens": counter.text(proj),
                               "project": t.project["name"]})
        return "\n\n".join(parts)

    async def _project_inline(self, t: TurnInput, counter: TokenCounter) -> str:
        out = [f"## Project: {t.project['name']}"]
        if instr := (t.project.get("instructions") or "").strip():
            out.append(f"Project instructions:\n{instr}")
        files = await self.store.project_files(t.project["id"])
        texts = []
        for f in files:
            texts.append((f, await self._attachment_text(f, t.spec)))
        total = sum(counter.text(x) for _, x in texts)
        if files and total <= BUDGETS["project_inline"]:
            out.append("Project knowledge (the full files):")
            for f, text in texts:
                src = self.sources.add(f["filename"], attachment_id=f["id"], snippet=text[:300])
                out.append(ContextBlock(f["filename"], "document", text,
                                        {"id": f["id"], "source": src.index}).render())
        elif files:
            names = ", ".join(f["filename"] for f in files)
            out.append(f"Project knowledge files: {names}. Relevant passages are provided with each "
                       "message; use file_search for more.")
        return "\n\n".join(out)

    async def _attachment_text(self, att: dict, spec: LLMSpec) -> str:
        prepared = await self.router.prepare(_att(att), spec)
        return "\n\n".join(c.text for c in prepared.context)

    # --- stage: per-turn context (dynamic) ----------------------------------------------
    async def _turn_context(self, t: TurnInput, query: str, stages: list, counter: TokenCounter) -> str:
        blocks = []
        if t.voice:
            # Per-turn, not in the system prompt: keeps the cached system+tools prefix identical
            # for typed and spoken turns (switching modes would otherwise re-prefill ~2K tokens).
            blocks.append(f"<voice_mode>\n{VOICE_RULES}\n</voice_mode>")
        if t.incognito:
            # Placed next to the user's words: models follow nearby reminders more reliably.
            blocks.append("<incognito_reminder>Memory is off in this chat. If asked to remember something, "
                          "say it can't be saved in an incognito chat.</incognito_reminder>")
        if self.memory is not None and t.prefs.get("memory_enabled", True) and not t.incognito and query.strip():
            mems = await self.memory.search(query, k=8)
            lines, used = [], 0
            for m in mems:
                n = counter.text(m["text"])
                if used + n > BUDGETS["memory"]:
                    break
                lines.append(f"- {m['text']}")
                used += n
            if lines:
                blocks.append("<user_memory note=\"facts the user asked you to remember; use when relevant\">\n"
                              + "\n".join(lines) + "\n</user_memory>")
                stages.append({"name": "memory", "tokens": used, "items": len(lines)})
        if t.project and query.strip():
            files = await self.store.project_files(t.project["id"])
            total = 0
            for f in files:
                total += counter.text(await self._attachment_text(f, t.spec))
            if total > BUDGETS["project_inline"]:
                await self._ensure_project_indexed(t, files)
                hits = await self.retriever.search(query, {"source_type": "project_file",
                                                           "project_id": t.project["id"]}, k=6)
                text = self._render_hits(hits, counter, BUDGETS["project_chunks"])
                if text:
                    blocks.append(text)
                    stages.append({"name": "project_retrieval", "tokens": counter.text(text), "hits": len(hits)})
        # Retrieval over this chat's earlier (long) attachments not inlined this turn.
        current_ids = {a["id"] for a in t.attachments}
        earlier = [a for a in await self.store.conversation_attachments(t.conversation["id"])
                   if a["id"] not in current_ids and a["source"] != "tool"]
        if earlier:
            listing = "\n".join(f"- {a['filename']} (id {a['id']}, {a.get('kind') or 'file'})" for a in earlier[-20:])
            blocks.append(f"<chat_files note=\"files attached earlier in this chat; read with file_read or "
                          f"file_search\">\n{listing}\n</chat_files>")
            if query.strip() and any(await asyncio.gather(*[self.store.has_chunks("attachment", a["id"])
                                                             for a in earlier])):
                hits = await self.retriever.search(query, {"source_type": "attachment",
                                                           "conversation_id": t.conversation["id"]}, k=4)
                text = self._render_hits(hits, counter, BUDGETS["retrieval"])
                if text:
                    blocks.append(text)
                    stages.append({"name": "attachment_retrieval", "tokens": counter.text(text), "hits": len(hits)})
        return "\n\n".join(blocks)

    def _render_hits(self, hits, counter: TokenCounter, budget: int) -> str:
        out, used = [], 0
        for h in hits:
            n = counter.text(h.text)
            if used + n > budget:
                break
            src = self.sources.add(h.meta.get("filename", "file"), attachment_id=h.source_id,
                                   locator=h.meta.get("label") or None, snippet=h.text[:300])
            out.append(ContextBlock(h.meta.get("filename", "file"), "document", h.text,
                                    {"source": src.index, "excerpt": h.meta.get("label") or "passage"}).render())
            used += n
        if not out:
            return ""
        return "<retrieved_passages note=\"most relevant excerpts; cite with [source]\">\n" + "\n".join(out) + \
            "\n</retrieved_passages>"

    async def _ensure_project_indexed(self, t: TurnInput, files: list[dict]) -> None:
        for f in files:
            if not await self.store.has_chunks("project_file", f["id"]):
                text = await self._attachment_text(f, t.spec)
                await self.retriever.index("project_file", f["id"], [("", text)], project_id=t.project["id"],
                                           extra_meta={"filename": f["filename"]})

    # --- stage: current user message ----------------------------------------------------
    def _current_user(self, t: TurnInput, turn_ctx: str) -> dict:
        parts: list[dict] = []
        for p, att in zip(t.prepared, t.attachments, strict=True):
            parts.extend(p.parts)
        blocks = []
        for p, att in zip(t.prepared, t.attachments, strict=True):
            for c in p.context:
                c.attrs.setdefault("id", att["id"])
                blocks.append(c.render())
        text = t.user_message["content"] or "(The user sent attachments without a message.)"
        pre = "\n\n".join(x for x in (f"<turn_context>\n{turn_ctx}\n</turn_context>" if turn_ctx else "",
                                      "\n\n".join(blocks)) if x)
        if not parts:
            return {"role": "user", "content": f"{pre}\n\n{text}" if pre else text}
        if pre:
            parts.append({"type": "text", "text": pre})
        parts.append({"type": "text", "text": text})
        return {"role": "user", "content": parts}

    # --- stage: history ------------------------------------------------------------------
    async def _history(self, t: TurnInput, budget: int, counter: TokenCounter) -> tuple[list[dict], str, dict]:
        rendered: list[tuple[dict, list[dict]]] = []   # (db message, openai messages)
        user_turns_back = 0
        for m in reversed(t.history):
            if m["role"] == "user":
                user_turns_back += 1
            rendered.append((m, await self._render_history_message(m, t, user_turns_back)))
        rendered.reverse()
        sizes = [counter.messages(msgs) for _, msgs in rendered]
        total = sum(sizes)
        meta = {"tokens": total, "kept": len(rendered), "summarized": 0}
        if total <= budget:
            return [x for _, msgs in rendered for x in msgs], "", meta

        # Too long: keep the most recent turns (~60% of budget) plus pinned; summarize the rest.
        keep_budget = int(max(budget, 0) * 0.6)
        keep_from = len(rendered)
        used = 0
        while keep_from > 0 and used + sizes[keep_from - 1] <= keep_budget:
            keep_from -= 1
            used += sizes[keep_from]
        while keep_from < len(rendered) and rendered[keep_from][0]["role"] != "user":
            used -= sizes[keep_from]   # start the kept window on a user turn
            keep_from += 1
        older = rendered[:keep_from]
        pinned = [(m, msgs) for m, msgs in older if m.get("pinned")]
        to_summarize = [m for m, _ in older if not m.get("pinned")]
        summary = await self._summary(t, to_summarize) if to_summarize else ""
        kept = [x for _, msgs in pinned for x in msgs] + [x for _, msgs in rendered[keep_from:] for x in msgs]
        meta.update(tokens=counter.messages(kept), kept=len(pinned) + len(rendered) - keep_from,
                    summarized=len(to_summarize), summary_tokens=counter.text(summary))
        return kept, summary, meta

    async def _render_history_message(self, m: dict, t: TurnInput, turns_back: int) -> list[dict]:
        if m["role"] == "assistant":
            return _assistant_to_openai(m, compress=turns_back > 1)
        parts: list[dict] = []
        blocks: list[str] = []
        for att in await self.store.attachments(m.get("attachment_ids") or []):
            try:
                p = await self.router.prepare(_att(att), t.spec)
            except Exception:  # noqa: BLE001
                blocks.append(f"[attachment {att['filename']} unavailable]")
                continue
            kind = p.kind
            if kind in (Kind.IMAGE, Kind.SCREENSHOT) and turns_back <= KEEP_IMAGES_TURNS:
                parts.extend(x for x in p.parts if x["type"] == "image_url")
            elif kind in (Kind.IMAGE, Kind.SCREENSHOT):
                blocks.append(f"[image {att['filename']} was shown earlier]")
            elif kind is Kind.VIDEO:
                blocks.append(f"[video {att['filename']} (id {att['id']}) was shown earlier as keyframes; "
                              "call analyze_media to look at a time range again]")
            for c in p.context:
                if kind is Kind.DOCUMENT and turns_back > KEEP_DOCS_TURNS and len(c.text) > 6000:
                    blocks.append(f"[document {att['filename']} (id {att['id']}) was attached earlier; "
                                  "use file_search or file_read to consult it]")
                    continue
                c.attrs.setdefault("id", att["id"])
                blocks.append(c.render())
            if att.get("voice_note"):
                blocks.append(ContextBlock(att["filename"], "note", att["voice_note"],
                                           {"id": att["id"], "about": "tone of voice"}).render())
        text = m["content"] or "(attachments only)"
        if not parts:
            return [{"role": "user", "content": "\n\n".join([*blocks, text]) if blocks else text}]
        if blocks:
            parts.append({"type": "text", "text": "\n\n".join(blocks)})
        parts.append({"type": "text", "text": text})
        return [{"role": "user", "content": parts}]

    async def _summary(self, t: TurnInput, msgs: list[dict]) -> str:
        cid = t.conversation["id"]
        upto = msgs[-1]["id"]
        for s in await self.store.summaries_for(cid):
            if s["upto_message_id"] == upto:
                return s["text"]
        await self.emit("status", {"stage": "summarizing", "message": "Summarizing earlier messages"})
        transcript = []
        for m in msgs:
            body = m["content"] or ""
            if m["role"] == "assistant":
                tools = [b["name"] for b in (m.get("blocks") or []) if b.get("type") == "tool_use"]
                if tools:
                    body = f"(used tools: {', '.join(tools)}) {body}"
            transcript.append(f"{m['role'].upper()}: {body}")
        text = "\n\n".join(transcript)
        if len(text) > 60_000:
            text = text[:20_000] + "\n\n[…]\n\n" + text[-40_000:]
        prompt = [
            {"role": "system", "content": "You write compact, factual summaries of conversations for another "
             "assistant to continue from. Keep names, numbers, decisions, open questions, the user's goals "
             "and preferences, and any files or artifacts mentioned. 150-400 words. No preamble."},
            {"role": "user", "content": f"Summarize this conversation so far:\n\n{text}"},
        ]
        out = []
        async for ev in self.manager.provider.chat_stream(ChatRequest(model=t.spec.id, messages=prompt,
                                                                      max_tokens=700, temperature=0.2)):
            if isinstance(ev, TextDelta):
                out.append(ev.text)
        summary = "".join(out).strip()
        if summary:
            await self.store.save_summary(cid, upto, summary, TokenCounter(t.spec).text(summary))
        return summary


def _att(row: dict) -> Attachment:
    return Attachment(Path(row["path"]), filename=row["filename"], mime=row["mime"],
                      source=row["source"], id=row["id"], sha256=row["sha256"])


def _assistant_to_openai(m: dict, compress: bool) -> list[dict]:
    """Expand stored steps into assistant/tool messages; thinking is not replayed."""
    out: list[dict] = []
    text: list[str] = []
    calls: list[dict] = []
    results: list[dict] = []
    limit = 1500 if compress else 6000

    def flush_tools():
        nonlocal calls, results, text
        if calls:
            out.append({"role": "assistant", "content": "".join(text), "tool_calls": calls})
            out.extend(results)
            calls, results, text = [], [], []

    for b in m.get("blocks") or []:
        kind = b.get("type")
        if kind == "text":
            if results:
                flush_tools()
            text.append(b["text"])
        elif kind == "tool_use":
            if results:
                flush_tools()
            calls.append({"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b.get("arguments", {}))}})
        elif kind == "tool_result":
            content = b.get("content", "")
            if len(content) > limit:
                content = content[:limit] + " … [clipped]"
            results.append({"role": "tool", "tool_call_id": b["id"], "name": b.get("name", ""),
                            "content": f'<tool_output name="{b.get("name", "")}" trust="untrusted">\n'
                                       f"{content}\n</tool_output>"})
    flush_tools()
    final = "".join(text).strip() or (m["content"] or "").strip()
    if not final:
        final = "[reply interrupted]" if m.get("status") in ("stopped", "error") else "[no reply]"
    out.append({"role": "assistant", "content": final})
    return out


def _sanitize(messages: list[dict]) -> list[dict]:
    """Debug copy of the prompt with base64 media replaced by short placeholders."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for p in c:
                if p.get("type") == "image_url":
                    parts.append({"type": "image_url", "image_url": {"url": "[image]"}})
                elif p.get("type") in ("input_audio", "input_video"):
                    parts.append({"type": p["type"], "path": next(iter(p[p["type"]].values()), "")})
                else:
                    parts.append(p)
            m = {**m, "content": parts}
        out.append(m)
    return out
