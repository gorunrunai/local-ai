"""Storage (branching, vectors, artifacts), enrichment stages and the agent loop — no real models."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio

from inference.types import StreamError, TextDelta
from media.cache import MediaCache
from media.router import ModalityRouter
from media.types import ContextBlock, Kind, PreparedAttachment
from orchestrator.agent.loop import Agent, AgentSettings, ConfirmationBroker
from orchestrator.pipeline import PromptBuilder, TurnInput, _assistant_to_openai
from orchestrator.policy import Policy, ToolRegistry
from orchestrator.rag import MemoryService, Retriever, chunk_text
from orchestrator.storage.db import Database
from orchestrator.storage.store import Store
from orchestrator.tools.base import SourceRegistry, Tool, ToolContext, ToolResult, render_source
from tests.unit.fakes import (
    DIMS,
    FakeEmbedder,
    ScriptedProvider,
    spec,
    text_turn,
    thinking_turn,
    tool_turn,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"


@pytest_asyncio.fixture
async def store():
    db = await Database(":memory:", DIMS).open()
    yield Store(db)
    await db.close()


# --- storage ---------------------------------------------------------------------------------
async def test_branching_paths(store):
    c = await store.create_conversation()
    u1 = await store.add_message(c["id"], None, "user", "apple?")
    a1 = await store.add_message(c["id"], u1["id"], "assistant", "APPLE")
    u2 = await store.add_message(c["id"], a1["id"], "user", "cherry?")
    await store.add_message(c["id"], u2["id"], "assistant", "CHERRY")
    edit = await store.add_message(c["id"], None, "user", "banana?")  # sibling of u1
    await store.add_message(c["id"], edit["id"], "assistant", "BANANA")
    assert [m["content"] for m in await store.active_path(c["id"])] == ["banana?", "BANANA"]
    leaf = await store.latest_leaf(u1["id"])
    await store.update_conversation(c["id"], active_leaf_id=leaf)
    assert [m["content"] for m in await store.active_path(c["id"])] == ["apple?", "APPLE", "cherry?", "CHERRY"]
    assert len(await store.children(c["id"], None)) == 2


async def test_incognito_ids_and_delete_cascade(tmp_path):
    db = await Database(":memory:", DIMS).open()
    s = Store(db, incognito=True)
    c = await s.create_conversation()
    assert c["id"].startswith("inc_") and c["incognito"]
    m = await s.add_message(c["id"], None, "user", "hi")
    await s.add_chunks([{"source_type": "message", "source_id": m["id"], "conversation_id": c["id"],
                         "text": "hi there"}], (await FakeEmbedder().embed(["hi there"])))
    await s.delete_conversation(c["id"])
    assert await s.get_message(m["id"]) is None
    assert await db.scalar("SELECT COUNT(*) FROM chunks") == 0
    await db.close()


async def test_artifact_versions(store):
    c = await store.create_conversation()
    v1 = await store.save_artifact_version(c["id"], "page", "html", "Page", "<b>1</b>", None)
    v2 = await store.save_artifact_version(c["id"], "page", "html", "Page v2", "<b>2</b>", None)
    assert (v1["version"], v2["version"], v1["id"]) == (1, 2, v2["id"])
    art = await store.get_artifact(v1["id"])
    assert [v["content"] for v in art["versions"]] == ["<b>1</b>", "<b>2</b>"] and art["title"] == "Page v2"


async def test_hybrid_search_and_scope(store):
    r = Retriever(store, FakeEmbedder(), None)
    await r.index("attachment", "a1", [("", "The launch window opens in March for Project Falcon.")],
                  conversation_id="c1", extra_meta={"filename": "brief.md"})
    await r.index("attachment", "a2", [("", "Pancakes need flour, eggs and milk.")], conversation_id="c2")
    hits = await r.search("When does the Falcon launch window open?", {"conversation_id": "c1"})
    assert hits and hits[0].source_id == "a1" and hits[0].meta["filename"] == "brief.md"
    assert not [h for h in await r.search("flour eggs", {"conversation_id": "c1"}) if h.source_id == "a2"]


async def test_memory_dedupes_and_retrieves(store):
    mem = MemoryService(store, FakeEmbedder())
    a = await mem.save("The user's dog is named Biscuit.", None)
    b = await mem.save("The user's dog is named Biscuit!", None)
    assert b["updated"] and a["id"] == b["id"] and len(await store.list_memories()) == 1
    await mem.save("The user lives in Lisbon.", None)
    hits = await mem.search("what is my dog named", max_distance=0.9)
    assert hits and "Biscuit" in hits[0]["text"]


def test_chunking_respects_size_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(10))
    chunks = chunk_text(text, size=800, overlap=100)
    assert all(len(c) <= 800 + 10 for c in chunks) and len(chunks) > 5
    assert chunk_text("short") == ["short"] and chunk_text("  ") == []


# --- enrichment pipeline -----------------------------------------------------------------------
class FakeManager:
    def __init__(self, provider=None):
        self.provider = provider or ScriptedProvider([])

    def llm_spec(self, _=None):
        return spec()


def _stt():
    return SimpleNamespace(spec=SimpleNamespace(id="x"))


async def _builder(store, tmp_path, provider=None, emit=None):
    router = ModalityRouter(_stt, MediaCache(tmp_path / "cache"), ocr=lambda p: SimpleNamespace(text=""))
    events = []

    async def _emit(e, d):
        events.append((e, d))

    b = PromptBuilder(store, Retriever(store, FakeEmbedder(), None), MemoryService(store, FakeEmbedder()),
                      router, FakeManager(provider), SourceRegistry(), emit or _emit)
    return b, events


def _turn(conv, user, history=(), prepared=(), attachments=(), prefs=None, incognito=False, project=None,
          tools=(), s=None):
    return TurnInput(conversation=conv, history=list(history), user_message=user, prepared=list(prepared),
                     attachments=list(attachments), spec=s or spec(), prefs={"max_tokens": 512, **(prefs or {})},
                     incognito=incognito, project=project, tools=list(tools))


async def test_system_stage_style_instructions_incognito(store, tmp_path):
    b, _ = await _builder(store, tmp_path)
    conv = await store.create_conversation(style="concise")
    user = await store.add_message(conv["id"], None, "user", "hello")
    out = await b.build(_turn(conv, user, prefs={"custom_instructions": "Call me Sam."}, incognito=True))
    system = out.messages[0]["content"]
    assert "Lead with the answer" in system and "Call me Sam." in system and "incognito" in system
    assert "<attachment_data>" in system or "attachment_data" in system
    last = out.messages[-1]["content"]
    assert last.endswith("hello") and "incognito_reminder" in last
    out = await b.build(_turn(conv, user))
    assert out.messages[-1] == {"role": "user", "content": "hello"}


async def test_memory_stage_injects_relevant_facts_only_when_allowed(store, tmp_path):
    await MemoryService(store, FakeEmbedder()).save("The user's dog is named Biscuit.", None)
    b, _ = await _builder(store, tmp_path)
    conv = await store.create_conversation()
    user = await store.add_message(conv["id"], None, "user", "What is my dog named?")
    out = await b.build(_turn(conv, user))
    assert "Biscuit" in out.messages[-1]["content"] and "user_memory" in out.messages[-1]["content"]
    out = await b.build(_turn(conv, user, prefs={"memory_enabled": False}))
    assert "Biscuit" not in json.dumps(out.messages)
    b.memory = None  # incognito path passes no memory service
    out = await b.build(_turn(conv, user, incognito=True))
    assert "Biscuit" not in json.dumps(out.messages)


async def test_project_stage_inlines_small_knowledge(store, tmp_path):
    b, _ = await _builder(store, tmp_path)
    proj = await store.create_project("Falcon", "Answer in French.")
    f = tmp_path / "brief.md"
    f.write_text("Codename ORCHID-7719.")
    att = await store.add_attachment(sha256="0" * 64, filename="brief.md", mime="text/markdown",
                                     size=f.stat().st_size, path=str(f), kind="document", source="upload",
                                     conversation_id=None)
    await store.add_project_file(proj["id"], att["id"])
    conv = await store.create_conversation(project_id=proj["id"])
    user = await store.add_message(conv["id"], None, "user", "codename?")
    out = await b.build(_turn(conv, user, project=proj))
    system = out.messages[0]["content"]
    assert "Answer in French." in system and "ORCHID-7719" in system and 'source="1"' in system


async def test_current_turn_media_and_context_blocks(store, tmp_path):
    b, _ = await _builder(store, tmp_path)
    conv = await store.create_conversation()
    user = await store.add_message(conv["id"], None, "user", "what's this?")
    p = PreparedAttachment("a1", "shot.png", Kind.SCREENSHOT, "native",
                           parts=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}],
                           context=[ContextBlock("shot.png", "ocr", "ERROR 42")])
    out = await b.build(_turn(conv, user, prepared=[p], attachments=[{"id": "att_1", "filename": "shot.png"}]))
    parts = out.messages[-1]["content"]
    assert parts[0]["type"] == "image_url" and 'id="att_1"' in parts[1]["text"] and parts[-1]["text"] == "what's this?"
    assert out.debug["messages"][-1]["content"][0]["image_url"]["url"] == "[image]"  # debug view sanitized


async def test_history_includes_tool_turns_and_summarizes_when_long(store, tmp_path):
    provider = ScriptedProvider([text_turn("SUMMARY: user asked many things about apples.")])
    b, events = await _builder(store, tmp_path, provider)
    conv = await store.create_conversation()
    parent, history = None, []
    for i in range(12):
        u = await store.add_message(conv["id"], parent, "user", f"question {i} " + "apple " * 150)
        a = await store.add_message(conv["id"], u["id"], "assistant", f"answer {i}")
        history += [u, a]
        parent = a["id"]
    await store.update_message(history[1]["id"], pinned=1)
    history[1]["pinned"] = 1
    user = await store.add_message(conv["id"], parent, "user", "final question")
    small = spec(context_length=2800)
    out = await b.build(_turn(conv, user, history=history, s=small))
    assert "SUMMARY: user asked many things" in out.messages[0]["content"]
    assert any(e == "status" for e, _ in events)
    contents = json.dumps(out.messages)
    assert "answer 0" in contents          # pinned message survives
    assert "answer 11" in contents         # most recent turn kept
    assert "question 3 " not in contents   # older turns summarized away
    stage = next(s for s in out.debug["stages"] if s["name"] == "history")
    assert stage["summarized"] > 0
    # the summary is cached: a second build doesn't call the model again
    await b.build(_turn(conv, user, history=history, s=small))
    assert len(provider.requests) == 1


def test_assistant_blocks_become_tool_messages():
    m = {"content": "Done: 42", "status": "complete", "blocks": [
        {"type": "thinking", "text": "hmm"},
        {"type": "text", "text": "Let me compute."},
        {"type": "tool_use", "id": "c1", "name": "code_exec", "arguments": {"code": "print(6*7)"}},
        {"type": "tool_result", "id": "c1", "name": "code_exec", "ok": True, "content": "42"},
        {"type": "text", "text": "Done: 42"}]}
    out = _assistant_to_openai(m, compress=False)
    assert out[0]["tool_calls"][0]["function"]["name"] == "code_exec" and out[0]["content"] == "Let me compute."
    assert out[1]["role"] == "tool" and "42" in out[1]["content"] and "untrusted" in out[1]["content"]
    assert out[2] == {"role": "assistant", "content": "Done: 42"}
    assert "hmm" not in json.dumps(out)  # thinking isn't replayed


# --- agent loop --------------------------------------------------------------------------------
class EchoTool(Tool):
    name = "echo"
    description = "echo text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self, side_effect=False):
        self.side_effect = side_effect
        self.calls = []

    async def run(self, args, ctx):
        self.calls.append(args)
        src = ctx.sources.add("Echo page", url="https://example.com/echo")
        return ToolResult(render_source(src, f"echo: {args['text']}"), data={"source": src.index},
                          images=[str(FIX / "chart.png")] if args["text"] == "img" else [])


async def _agent(store, provider, tool, *, overrides=None, max_iterations=4, decide=None):
    conv = await store.create_conversation()
    msg = await store.add_message(conv["id"], None, "assistant", "")
    registry = ToolRegistry(config={"tools": {}, "mcp": {}})
    registry.builtins = {"echo": tool}
    events = []
    broker = ConfirmationBroker()

    async def emit(e, d):
        events.append((e, d))
        if e == "tool_confirmation" and decide is not None:
            asyncio.get_running_loop().call_soon(broker.resolve, d["id"], decide)

    ctx = ToolContext(conversation_id=conv["id"], message_id=msg["id"], project_id=None, incognito=False,
                      store=store, retriever=None, memory=None, router=None, manager=None, files=None,
                      sources=SourceRegistry(), emit=emit, model_id="m", workdir_root=None)
    agent = Agent(provider, registry, Policy({"tools": {}, "mcp": {}}), ctx, broker, emit, asyncio.Event(),
                  AgentSettings(model="m", max_iterations=max_iterations, overrides=overrides or {},
                                confirmation_timeout_s=2))
    return agent, events


async def test_agent_runs_tool_then_answers_with_citation(store):
    tool = EchoTool()
    provider = ScriptedProvider([tool_turn("echo", '{"text": "hi"}'), text_turn("It said hi [1].")])
    agent, _ = await _agent(store, provider, tool)
    out = await agent.run([{"role": "user", "content": "echo hi"}])
    assert tool.calls == [{"text": "hi"}] and out.text == "It said hi [1]."
    assert out.citations[0]["url"] == "https://example.com/echo" and not out.citations[0].get("implicit")
    second = provider.requests[1].messages
    assert second[-2]["tool_calls"][0]["function"]["name"] == "echo"
    assert "trust=\"untrusted\"" in second[-1]["content"] and "Sources in this result: [1]" in second[-1]["content"]
    assert [b["type"] for b in out.blocks] == ["tool_use", "tool_result", "text"]


async def test_agent_attaches_implicit_sources_when_model_forgets_to_cite(store):
    provider = ScriptedProvider([tool_turn("echo", '{"text": "hi"}'), text_turn("It said hi.")])
    agent, _ = await _agent(store, provider, EchoTool())
    out = await agent.run([{"role": "user", "content": "echo hi"}])
    assert out.citations and out.citations[0]["implicit"]


async def test_invalid_arguments_get_repair_message(store):
    tool = EchoTool()
    provider = ScriptedProvider([tool_turn("echo", '{"txt": 1}'), tool_turn("echo", '{"text": "ok"}', "c2"),
                                 text_turn("fixed")])
    agent, _ = await _agent(store, provider, tool)
    out = await agent.run([{"role": "user", "content": "x"}])
    repair = provider.requests[1].messages[-1]["content"]
    assert "Invalid arguments" in repair and "'text' is a required property" in repair
    assert tool.calls == [{"text": "ok"}] and out.text == "fixed"


async def test_unknown_tool_reports_available_tools(store):
    provider = ScriptedProvider([tool_turn("nope", "{}"), text_turn("ok")])
    agent, _ = await _agent(store, provider, EchoTool())
    await agent.run([{"role": "user", "content": "x"}])
    assert "Unknown tool 'nope'" in provider.requests[1].messages[-1]["content"]


async def test_text_fallback_tool_call_is_executed_and_hidden(store):
    tool = EchoTool()
    provider = ScriptedProvider([[TextDelta('On it. <tool_call>{"name": "echo", "arguments": {"text": "a"}}</tool_call>')],
                                 text_turn("done")])
    agent, events = await _agent(store, provider, tool)
    out = await agent.run([{"role": "user", "content": "x"}])
    shown = "".join(d["text"] for e, d in events if e == "text_delta")
    assert tool.calls == [{"text": "a"}] and "<tool_call>" not in shown and out.text == "On it.\n\ndone"


async def test_confirmation_approved_and_denied(store):
    for approve, expect_calls in ((True, 1), (False, 0)):
        tool = EchoTool(side_effect=True)
        provider = ScriptedProvider([tool_turn("echo", '{"text": "x"}'), text_turn("ok")])
        agent, events = await _agent(store, provider, tool, decide=approve)
        await agent.run([{"role": "user", "content": "x"}])
        assert len(tool.calls) == expect_calls
        assert any(e == "tool_confirmation" for e, _ in events)
        result = next(d for e, d in events if e == "tool_result")
        assert result["decision"] == ("confirm-approved" if approve else "confirm-denied")


async def test_iteration_cap_forces_an_answer(store):
    tool = EchoTool()
    provider = ScriptedProvider([tool_turn("echo", '{"text": "1"}', "a"), tool_turn("echo", '{"text": "2"}', "b"),
                                 text_turn("final")])
    agent, _ = await _agent(store, provider, tool, max_iterations=2)
    out = await agent.run([{"role": "user", "content": "loop"}])
    assert provider.requests[2].tools is None and "tool-call limit" in json.dumps(provider.requests[2].messages)
    assert out.text == "final" and len(tool.calls) == 2


async def test_tool_images_follow_as_user_content(store):
    provider = ScriptedProvider([tool_turn("echo", '{"text": "img"}'), text_turn("saw it")])
    agent, _ = await _agent(store, provider, EchoTool())
    await agent.run([{"role": "user", "content": "x"}])
    last = provider.requests[1].messages[-1]
    assert last["role"] == "user" and last["content"][1]["type"] == "image_url"


async def test_thinking_streamed_separately_and_errors_surface(store):
    provider = ScriptedProvider([thinking_turn("let me think", "42")])
    agent, events = await _agent(store, provider, EchoTool())
    out = await agent.run([{"role": "user", "content": "x"}])
    assert ("thinking_delta", {"text": "let me think"}) in events and out.blocks[0]["type"] == "thinking"
    provider = ScriptedProvider([[StreamError("boom", 500)]])
    agent, events = await _agent(store, provider, EchoTool())
    out = await agent.run([{"role": "user", "content": "x"}])
    assert out.finish_reason == "error" and any(e == "error" for e, _ in events)


async def test_stop_cancels_generation(store):
    provider = ScriptedProvider([[TextDelta("a"), TextDelta("b"), TextDelta("c")]])
    agent, _ = await _agent(store, provider, EchoTool())
    agent.cancel.set()
    out = await agent.run([{"role": "user", "content": "x"}])
    assert out.finish_reason == "stopped" and out.text == ""
