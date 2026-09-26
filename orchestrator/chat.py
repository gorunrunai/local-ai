"""Application state and the per-turn runner behind the chat API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from inference.manager import ModelManager
from inference.types import ChatRequest, TextDelta
from media.cache import MediaCache
from media.router import ModalityRouter, RouteOptions
from media.types import ContextBlock, Kind, Progress
from orchestrator.agent.loop import Agent, AgentSettings, ConfirmationBroker
from orchestrator.mcp import MCPManager
from orchestrator.pipeline import PromptBuilder, TurnInput, _att
from orchestrator.policy import Policy, ToolRegistry, tools_config
from orchestrator.rag import MemoryService, Retriever
from orchestrator.settings import DEFAULT_PREFS, AppSettings
from orchestrator.storage.db import Database
from orchestrator.storage.files import FileStore
from orchestrator.storage.store import Store
from orchestrator.tools.base import SourceRegistry, ToolContext
from orchestrator.voice_note import listener_available, voice_note

log = logging.getLogger(__name__)
INDEX_MIN_CHARS = 4000  # attachments with more text than this are indexed for retrieval


@dataclass
class AppState:
    settings: AppSettings
    manager: ModelManager
    persistent: Store
    ephemeral: Store                 # in-memory DB for incognito chats
    files: FileStore
    tmp_files: FileStore             # incognito uploads; wiped on shutdown
    router: ModalityRouter
    tmp_router: ModalityRouter       # incognito media cache (tmp)
    registry: ToolRegistry
    policy: Policy
    mcp: MCPManager
    confirmations: ConfirmationBroker = field(default_factory=ConfirmationBroker)
    runs: dict[str, asyncio.Event] = field(default_factory=dict)
    background: set = field(default_factory=set)

    @classmethod
    async def create(cls, settings: AppSettings, manager: ModelManager | None = None,
                     start_models: bool = True) -> AppState:
        manager = manager or ModelManager()
        if start_models:
            await manager.start()
        dims = manager.embedder().spec.dims or 768
        persistent = Store(await Database(settings.db_path, dims).open())
        ephemeral = Store(await Database(":memory:", dims).open(), incognito=True)
        tmp = Path(settings.tmp_dir)
        shutil.rmtree(tmp / "incognito", ignore_errors=True)
        mcp = MCPManager(tmp_dir=tmp / "mcp")
        await mcp.start()
        state = cls(settings=settings, manager=manager, persistent=persistent, ephemeral=ephemeral,
                   files=FileStore(settings.files_dir), tmp_files=FileStore(tmp / "incognito" / "files"),
                   router=ModalityRouter(manager.stt, MediaCache(settings.data_dir / "cache" / "media")),
                   tmp_router=ModalityRouter(manager.stt, MediaCache(tmp / "incognito" / "cache")),
                   registry=ToolRegistry(mcp), policy=Policy(), mcp=mcp)
        state.apply_prefs(await state.prefs())
        return state

    async def close(self) -> None:
        for ev in self.runs.values():
            ev.set()
        if self.background:
            await asyncio.wait(self.background, timeout=10)
        await self.mcp.stop()
        await self.persistent.db.close()
        await self.ephemeral.db.close()
        shutil.rmtree(Path(self.settings.tmp_dir) / "incognito", ignore_errors=True)
        await self.manager.stop()

    # --- routing between the persistent and the incognito world -------------------------
    @staticmethod
    def is_incognito(cid: str | None) -> bool:
        return bool(cid and cid.startswith("inc_"))

    def store_for(self, cid: str | None) -> Store:
        return self.ephemeral if self.is_incognito(cid) else self.persistent

    def router_for(self, cid: str | None) -> ModalityRouter:
        return self.tmp_router if self.is_incognito(cid) else self.router

    def files_for(self, cid: str | None) -> FileStore:
        return self.tmp_files if self.is_incognito(cid) else self.files

    def retriever_for(self, store: Store) -> Retriever:
        return Retriever(store, self.manager.embedder(), self.manager.reranker())

    def memory_for(self, store: Store) -> MemoryService | None:
        return None if store.incognito else MemoryService(self.persistent, self.manager.embedder())

    async def prefs(self) -> dict:
        stored = await self.persistent.get_setting("prefs", {})
        merged = {**DEFAULT_PREFS, **stored}
        merged["custom_styles"] = await self.persistent.get_setting("custom_styles", {})
        return merged

    def apply_prefs(self, prefs: dict) -> None:
        """Settings that change runtime behaviour (not just prompts)."""
        stt = (prefs.get("voice") or {}).get("stt")
        if stt in self.manager.cfg.stt:
            self.manager.cfg.defaults.stt = stt

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)


@dataclass
class TurnRequest:
    conversation_id: str
    text: str = ""
    attachment_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None          # branch point; default = active leaf
    regenerate_user_message_id: str | None = None
    model: str | None = None
    thinking: bool = False
    voice: bool = False            # spoken turn: conversational style, short replies


class ChatService:
    def __init__(self, state: AppState):
        self.s = state

    async def warm_up(self) -> None:
        """Prime the model's prefix cache with the real system prompt + tools, and warm the
        embedder, so the first real turn (typed or spoken) is as fast as later ones."""
        s = self.s
        try:
            model_id = s.manager.cfg.defaults.llm
            await s.manager.ensure_llm(model_id)
            prefs = await s.prefs()
            spec = s.manager.llm_spec(model_id)
            memory = s.memory_for(s.persistent)
            conv = {"id": "warmup", "style": None, "project_id": None}
            ctx = ToolContext(conversation_id="warmup", message_id="warmup", project_id=None, incognito=False,
                              store=s.persistent, retriever=s.retriever_for(s.persistent), memory=memory,
                              router=s.router, manager=s.manager, files=s.files, sources=SourceRegistry(),
                              emit=_noop_emit, model_id=model_id, workdir_root=None)
            agent = Agent(s.manager.provider, s.registry, s.policy, ctx, s.confirmations, _noop_emit,
                          asyncio.Event(), AgentSettings(model=model_id, overrides=prefs.get("tool_overrides", {})))
            built = await PromptBuilder(s.persistent, ctx.retriever, memory, s.router, s.manager, ctx.sources,
                                        _noop_emit).build(TurnInput(
                conversation=conv, history=[], user_message={"content": "hi"}, prepared=[], attachments=[],
                spec=spec, prefs=prefs, incognito=False, project=None, tools=agent.tool_definitions()))
            req = ChatRequest(model=model_id, messages=built.messages, tools=agent.tool_definitions() or None,
                              max_tokens=1, temperature=0)
            async for _ in s.manager.provider.chat_stream(req):
                pass
            log.info("prefix cache primed (%s prompt tokens)", built.debug["total_prompt_tokens"])
        except Exception:
            log.warning("warm-up failed", exc_info=True)

    async def run_turn(self, req: TurnRequest) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event, data) pairs for one user turn, persisting everything as it goes."""
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        async def emit(event: str, data: dict) -> None:
            await queue.put((event, data))

        cancel = asyncio.Event()
        self.s.runs[req.conversation_id] = cancel

        async def work():
            try:
                await self._turn(req, emit, cancel)
            except Exception as e:
                log.exception("turn failed")
                await emit("error", {"message": f"{type(e).__name__}: {e}"})
            finally:
                await queue.put(done)

        task = asyncio.create_task(work())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield item
        finally:
            if not task.done():   # client went away: stop generating, but let the turn persist
                cancel.set()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, 30)
            if self.s.runs.get(req.conversation_id) is cancel:
                self.s.runs.pop(req.conversation_id, None)

    async def _turn(self, req: TurnRequest, emit, cancel: asyncio.Event) -> None:
        s = self.s
        store = s.store_for(req.conversation_id)
        incognito = store.incognito
        conv = await store.get_conversation(req.conversation_id)
        if not conv:
            await emit("error", {"message": "conversation not found", "status": 404})
            return
        prefs = await s.prefs()
        model_id = req.model or conv.get("model") or s.manager.cfg.defaults.llm
        spec = s.manager.llm_spec(model_id)

        # 1. user message (new, edited branch, or regenerate)
        if req.regenerate_user_message_id:
            user_msg = await store.get_message(req.regenerate_user_message_id)
            if not user_msg or user_msg["role"] != "user":
                await emit("error", {"message": "message to regenerate not found", "status": 404})
                return
        else:
            parent = req.parent_id if req.parent_id is not None else conv.get("active_leaf_id")
            if parent == "":
                parent = None
            user_msg = await store.add_message(conv["id"], parent, "user", req.text, req.attachment_ids)
            for aid in req.attachment_ids:
                att = await store.get_attachment(aid)
                if att and not att["conversation_id"]:
                    await store.update_attachment(aid, conversation_id=conv["id"])
        assistant = await store.add_message(conv["id"], user_msg["id"], "assistant", "", model=model_id,
                                            status="streaming")
        await emit("message_start", {"conversation_id": conv["id"], "user_message": _public(user_msg),
                                     "assistant_message_id": assistant["id"], "model": model_id,
                                     "incognito": incognito})

        if model_id not in await s.manager.running_llms():
            await emit("status", {"stage": "loading_model", "message": f"Loading {spec.display_name}"})
        await s.manager.ensure_llm(model_id)

        # 2. attachments for this turn
        router = s.router_for(conv["id"])
        attachments = await store.attachments(user_msg.get("attachment_ids") or [])

        async def on_progress(p: Progress) -> None:
            await emit("media_progress", {"attachment_id": p.attachment_id, "stage": p.stage,
                                          "fraction": round(p.fraction, 3), "message": p.message})

        opts = RouteOptions(frame_budget=prefs.get("video_frame_budget"))
        prepared = await router.prepare_all([_att(a) for a in attachments], spec, opts, on_progress)
        for p, att in zip(prepared, attachments, strict=True):
            if p.kind != att.get("kind"):
                await store.update_attachment(att["id"], kind=str(p.kind))
            if p.warnings:
                await emit("media_warning", {"attachment_id": att["id"], "warnings": p.warnings})
            if p.kind is Kind.AUDIO and not spec.capabilities.audio and await listener_available(s.manager):
                note = att.get("voice_note")
                if not note:
                    await emit("status", {"stage": "listening", "message": "Listening to the voice message"})
                    note = await voice_note(s.manager, router, att)
                    await store.update_attachment(att["id"], voice_note=note)
                    att["voice_note"] = note
                p.context.append(ContextBlock(att["filename"], "note", note, {"about": "tone of voice"}))
            text_len = sum(len(c.text) for c in p.context)
            if text_len > INDEX_MIN_CHARS and not incognito:
                s.spawn(self._index_attachment(store, att, "\n\n".join(c.text for c in p.context), conv["id"]))

        # 3. enrichment
        sources = SourceRegistry()
        project = await store.get_project(conv["project_id"]) if conv.get("project_id") else None
        retriever = s.retriever_for(store)
        memory = s.memory_for(store)
        overrides = prefs.get("tool_overrides", {})
        ctx = ToolContext(conversation_id=conv["id"], message_id=assistant["id"],
                          project_id=conv.get("project_id"), incognito=incognito, store=store,
                          retriever=retriever, memory=memory, router=router, manager=s.manager,
                          files=s.files_for(conv["id"]), sources=sources, emit=emit, model_id=model_id,
                          workdir_root=Path(s.settings.tmp_dir) / ("incognito/sandbox" if incognito else "sandbox"),
                          cancel=cancel)
        agent_cfg = tools_config()["agent"]
        agent = Agent(s.manager.provider, s.registry, s.policy, ctx, s.confirmations, emit, cancel,
                      AgentSettings(model=model_id,
                                    thinking=req.thinking and spec.capabilities.thinking and not req.voice,
                                    thinking_budget=spec.thinking_budget,
                                    temperature=prefs.get("temperature"),
                                    max_tokens=min(int(prefs.get("max_tokens") or 4096), 800) if req.voice
                                    else int(prefs.get("max_tokens") or 4096),
                                    max_iterations=agent_cfg["max_iterations"],
                                    output_max_chars=agent_cfg["tool_output_max_chars"],
                                    confirmation_timeout_s=agent_cfg["confirmation_timeout_s"],
                                    incognito=incognito, memory_enabled=prefs.get("memory_enabled", True),
                                    overrides=overrides, voice=req.voice),
                      on_usage=lambda u: s.manager.record_usage(model_id, u))
        history = await store.path_to(user_msg["parent_id"]) if user_msg["parent_id"] else []
        builder = PromptBuilder(store, retriever, memory, router, s.manager, sources, emit)
        built = await builder.build(TurnInput(
            conversation=conv, history=history, user_message=user_msg, prepared=prepared,
            attachments=attachments, spec=spec, prefs=prefs, incognito=incognito, project=project,
            tools=agent.tool_definitions() if spec.capabilities.tools else [],
            thinking=req.thinking, voice=req.voice))
        await store.save_prompt_debug(assistant["id"], {**built.debug, "sources": [
            src.to_dict() for src in sources.sources.values()]})
        await emit("prompt_ready", {"message_id": assistant["id"],
                                    "prompt_tokens": built.debug["total_prompt_tokens"]})

        # 4. generate (agent loop)
        outcome = await agent.run(built.messages)
        status = {"stopped": "stopped", "error": "error"}.get(outcome.finish_reason, "complete")
        await store.update_message(assistant["id"], content=outcome.text, blocks=outcome.blocks,
                                   citations=outcome.citations, usage=outcome.usage, status=status)
        await store.touch_conversation(conv["id"])
        await emit("usage", outcome.usage)

        # 5. title + search index
        if not conv.get("title") and status == "complete":
            title = await self._title(model_id, user_msg["content"] or ", ".join(a["filename"] for a in attachments),
                                      outcome.text)
            if title:
                await store.update_conversation(conv["id"], title=title)
                await emit("title", {"conversation_id": conv["id"], "title": title})
        if not incognito and status == "complete":
            s.spawn(self._index_messages(store, conv["id"], user_msg, assistant["id"], outcome.text))
        await emit("message_end", {"message_id": assistant["id"], "status": status,
                                   "finish_reason": outcome.finish_reason})

    async def _title(self, model_id: str, user_text: str, reply: str) -> str:
        req = ChatRequest(model=self.s.manager.cfg.defaults.title_llm or model_id, max_tokens=24,
                          temperature=0.3, messages=[
                              {"role": "system", "content": "Name this chat in 2-6 words, like a sidebar "
                               "label, based on what the user asked about (e.g. 'Fibonacci chart in Python', "
                               "'Oslo weather'). Reply with the title only: no quotes, no trailing punctuation."},
                              {"role": "user", "content": f"The user wrote:\n{user_text[:1500]}\n\n"
                               f"(The reply began: {reply[:300]})"}])
        out = []
        try:
            async for ev in self.s.manager.provider.chat_stream(req):
                if isinstance(ev, TextDelta):
                    out.append(ev.text)
        except Exception:  # noqa: BLE001
            return ""
        return "".join(out).strip().strip('"').strip()[:80]

    async def _index_messages(self, store: Store, cid: str, user_msg: dict, assistant_id: str, reply: str) -> None:
        try:
            r = self.s.retriever_for(store)
            if user_msg["content"].strip():
                await r.index("message", user_msg["id"], [("", user_msg["content"])], conversation_id=cid,
                              extra_meta={"role": "user"})
            if reply.strip():
                await r.index("message", assistant_id, [("", reply)], conversation_id=cid,
                              extra_meta={"role": "assistant"})
        except Exception:
            log.exception("message indexing failed")

    async def _index_attachment(self, store: Store, att: dict, text: str, cid: str) -> None:
        try:
            await self.s.retriever_for(store).index("attachment", att["id"], [("", text)], conversation_id=cid,
                                                    extra_meta={"filename": att["filename"]})
        except Exception:
            log.exception("attachment indexing failed")


async def _noop_emit(event: str, data: dict) -> None:
    return None


def _public(m: dict) -> dict:
    return {k: m[k] for k in ("id", "parent_id", "role", "content", "created_at", "attachment_ids") if k in m}
