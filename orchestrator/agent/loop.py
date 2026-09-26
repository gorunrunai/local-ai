"""The agentic loop: stream the model, run requested tools under the policy hook, repeat."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from inference.types import (
    ChatRequest,
    ReasoningDelta,
    StreamError,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from media.images import image_part
from orchestrator.agent.claims import correction, unbacked_claim
from orchestrator.agent.parser import (
    InlineCallFilter,
    ToolCall,
    calls_from_deltas,
    calls_from_text,
    cited_indices,
    parse_arguments,
)
from orchestrator.policy import Policy, ToolRegistry
from orchestrator.tools.base import Emit, ToolContext, ToolResult, wrap_tool_output

log = logging.getLogger(__name__)


class ConfirmationBroker:
    """Parks tool calls that need the user's approval until the UI answers."""

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    async def wait(self, call_id: str, timeout_s: float) -> bool:
        fut = asyncio.get_running_loop().create_future()
        self._pending[call_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: str, approve: bool) -> bool:
        fut = self._pending.get(call_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approve)
        return True

    def pending(self) -> list[str]:
        return list(self._pending)


@dataclass
class AgentSettings:
    model: str
    thinking: bool = False
    thinking_budget: int | None = None
    temperature: float | None = None
    max_tokens: int = 4096
    max_iterations: int = 8
    output_max_chars: int = 12_000
    confirmation_timeout_s: float = 300
    incognito: bool = False
    memory_enabled: bool = True
    overrides: dict = field(default_factory=dict)
    voice: bool = False           # spoken reply: nothing is shown unless a tool shows it


@dataclass
class AgentOutcome:
    text: str
    blocks: list[dict]
    citations: list[dict]
    usage: dict
    finish_reason: str
    messages: list[dict]  # full transcript sent to the model, incl. tool turns (for debugging)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n} chars clipped]"


class Agent:
    def __init__(self, provider, registry: ToolRegistry, policy: Policy, ctx: ToolContext,
                 confirmations: ConfirmationBroker, emit: Emit, cancel: asyncio.Event,
                 settings: AgentSettings, on_usage=None):
        self.provider = provider
        self.registry = registry
        self.policy = policy
        self.ctx = ctx
        self.confirmations = confirmations
        self.emit = emit
        self.cancel = cancel
        self.s = settings
        self.on_usage = on_usage

    def tool_definitions(self) -> list[dict]:
        return [t.definition() for t in self.registry.all().values()
                if self.policy.available(t, incognito=self.s.incognito, memory_enabled=self.s.memory_enabled,
                                         overrides=self.s.overrides)]

    async def run(self, messages: list[dict]) -> AgentOutcome:
        messages = list(messages)
        tools = self.tool_definitions()
        known = {t["function"]["name"] for t in tools}
        blocks: list[dict] = []
        final_text: list[str] = []
        cited: dict[int, dict] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "ttft_s": None, "decode_tok_s": None,
                 "iterations": 0}
        finish = "stop"
        checked_claims = False

        for iteration in range(self.s.max_iterations + 1):
            usage["iterations"] = iteration + 1
            last_round = iteration == self.s.max_iterations
            if last_round and tools:
                messages.append({"role": "user", "content": "[system note] The tool-call limit for this "
                                 "reply is reached. Answer now using what you have; do not call tools."})
            req = ChatRequest(model=self.s.model, messages=list(messages),  # snapshot per request
                              tools=None if (last_round or not tools) else tools,
                              thinking=self.s.thinking, thinking_budget=self.s.thinking_budget,
                              temperature=self.s.temperature, max_tokens=self.s.max_tokens)
            raw, visible, reasoning = [], [], []
            slots: dict[int, dict] = {}
            filt = InlineCallFilter()
            error = None
            async for ev in self.provider.chat_stream(req):
                if self.cancel.is_set():
                    finish = "stopped"
                    break
                if isinstance(ev, TextDelta):
                    raw.append(ev.text)
                    shown = filt.feed(ev.text)
                    if shown:
                        visible.append(shown)
                        await self.emit("text_delta", {"text": shown})
                        await self._emit_citations("\n\n".join([*final_text, "".join(visible)]), cited)
                elif isinstance(ev, ReasoningDelta):
                    reasoning.append(ev.text)
                    await self.emit("thinking_delta", {"text": ev.text})
                elif isinstance(ev, ToolCallDelta):
                    slot = slots.setdefault(ev.index, {"id": None, "name": "", "arguments": ""})
                    slot["id"] = slot["id"] or ev.id
                    slot["name"] += ev.name or ""
                    slot["arguments"] += ev.arguments
                elif isinstance(ev, Usage):
                    usage["prompt_tokens"] += ev.prompt_tokens or 0
                    usage["completion_tokens"] += ev.completion_tokens or 0
                    usage["ttft_s"] = usage["ttft_s"] or ev.ttft_s
                    usage["decode_tok_s"] = ev.decode_tok_s or usage["decode_tok_s"]
                    if self.on_usage:
                        self.on_usage(ev)
                elif isinstance(ev, StreamError):
                    error = ev
            if tail := filt.flush():
                visible.append(tail)
                await self.emit("text_delta", {"text": tail})
            if reasoning:
                blocks.append({"type": "thinking", "text": "".join(reasoning)})
            if error:
                await self.emit("error", {"message": error.message, "status": error.status})
                finish = "error"
                if visible:
                    blocks.append({"type": "text", "text": "".join(visible)})
                    final_text.append("".join(visible))
                break

            calls = calls_from_deltas(slots)
            text = "".join(visible)
            if not calls and known and not last_round:
                calls, cleaned = calls_from_text("".join(raw), known)
                if calls and cleaned != text:
                    text = cleaned
                    await self.emit("text_replace", {"text": "\n\n".join([*final_text, cleaned])})
            if text.strip():
                blocks.append({"type": "text", "text": text})
                final_text.append(text)
            if not calls and finish != "stopped" and not last_round and not checked_claims and text.strip():
                # Once per reply: a claim like "I've created the video" with no successful tool call
                # behind it gets corrected by the model itself, before the user relies on it.
                checked_claims = True
                done = {b["name"] for b in blocks if b.get("type") == "tool_result" and b.get("ok")}
                if claim := unbacked_claim(text, done, voice=self.s.voice):
                    log.info("unbacked claim, asking the model to correct it: %r", claim[0])
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": correction(*claim)})
                    continue
            if finish == "stopped" or not calls or last_round:
                break

            messages.append({"role": "assistant", "content": text,
                             "tool_calls": [c.to_openai() for c in calls]})
            image_followups: list[tuple[str, list[str]]] = []
            for call in calls:
                if self.cancel.is_set():
                    finish = "stopped"
                    break
                seen = set(self.ctx.sources.sources)
                result, block = await self._run_call(call)
                blocks.extend(block)
                content = wrap_tool_output(call.name, _clip(result.content, self.s.output_max_chars), result.ok)
                new = [self.ctx.sources.sources[i] for i in sorted(set(self.ctx.sources.sources) - seen)]
                if new:  # trusted note, outside the untrusted envelope
                    listing = "; ".join(f"[{src.index}] {src.title}" for src in new)
                    content += f"\nSources in this result: {listing}. Cite them by number when you use them."
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": content})
                if result.images:
                    image_followups.append((call.name, result.images))
            for name, imgs in image_followups:
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": f"[Images returned by {name}; treat as tool output data]"},
                    *[image_part(p) for p in imgs[:12]]]})
            if finish == "stopped":
                break

        if not cited and finish != "error":
            # The model used sources but cited none: attach what the tools consulted so the UI can
            # still show "Sources" (marked implicit, since no claim is linked to them).
            used = {b["data"].get("source") for b in blocks if b.get("type") == "tool_result"
                    and isinstance(b.get("data"), dict) and b["data"].get("source")}
            for n in sorted(x for x in used if isinstance(x, int)):
                if src := self.ctx.sources.get(n):
                    cited[n] = {**src.to_dict(), "implicit": True}
                    await self.emit("citation", cited[n])
        citations = [cited[k] for k in sorted(cited)]
        return AgentOutcome(text="\n\n".join(t.strip() for t in final_text if t.strip()), blocks=blocks, citations=citations, usage=usage,
                            finish_reason=finish, messages=messages)

    async def _emit_citations(self, text: str, cited: dict[int, dict]) -> None:
        for n in cited_indices(text):
            if n not in cited and (src := self.ctx.sources.get(n)):
                cited[n] = src.to_dict()
                await self.emit("citation", cited[n])

    async def _run_call(self, call: ToolCall) -> tuple[ToolResult, list[dict]]:
        tool = self.registry.get(call.name)
        await self.emit("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments,
                                      "status": "pending"})
        started = time.time()
        decision_label = "allow"
        if tool is None:
            available = ", ".join(sorted(t["function"]["name"] for t in self.tool_definitions()))
            result = ToolResult(f"Unknown tool '{call.name}'. Available tools: {available}.", ok=False)
            args = {}
            decision_label = "invalid"
        else:
            args, err = parse_arguments(call.arguments, tool.parameters)
            if err:
                result = ToolResult(f"Invalid arguments for {call.name}: {err}. Call the tool again with "
                                    f"arguments matching its schema.", ok=False)
                args = {"_raw": call.arguments}
                decision_label = "invalid"
            else:
                decision = self.policy.decide(tool, args, incognito=self.s.incognito,
                                              memory_enabled=self.s.memory_enabled, overrides=self.s.overrides)
                approved = decision.action == "allow"
                if decision.action == "confirm":
                    await self.emit("tool_confirmation", {"id": call.id, "name": call.name, "arguments": args,
                                                          "reason": decision.reason})
                    approved = await self.confirmations.wait(call.id, self.s.confirmation_timeout_s)
                    decision_label = "confirm-approved" if approved else "confirm-denied"
                elif decision.action == "deny":
                    decision_label = "deny"
                if not approved:
                    why = decision.reason if decision.action == "deny" else "the user declined"
                    result = ToolResult(f"Tool call not run: {why}. Continue without it.", ok=False)
                else:
                    await self.emit("tool_call", {"id": call.id, "name": call.name, "arguments": args,
                                                  "status": "running"})
                    self.ctx.call_id = call.id
                    try:
                        result = await tool.run(args, self.ctx)
                    except Exception as e:
                        log.exception("tool %s failed", call.name)
                        result = ToolResult(f"Tool error: {type(e).__name__}: {e}", ok=False)
        duration_ms = int((time.time() - started) * 1000)
        status = "ok" if result.ok else ("denied" if decision_label in ("deny", "confirm-denied") else "error")
        # Stored in the chat's own DB (the in-memory one for incognito chats).
        await self.ctx.store.add_tool_event(message_id=self.ctx.message_id, call_id=call.id,
                                            name=call.name, args=args,
                                            result=result.data or {"content": result.content[:2000]},
                                            status=status, decision=decision_label, started_at=started,
                                            duration_ms=duration_ms)
        preview = result.content if len(result.content) < 1500 else result.content[:1500] + " …"
        await self.emit("tool_result", {"id": call.id, "name": call.name, "ok": result.ok, "status": status,
                                        "decision": decision_label, "duration_ms": duration_ms,
                                        "preview": preview, "data": result.data, "files": result.files})
        blocks = [
            {"type": "tool_use", "id": call.id, "name": call.name, "arguments": args, "decision": decision_label},
            {"type": "tool_result", "id": call.id, "name": call.name, "ok": result.ok, "status": status,
             "content": _clip(result.content, 4000), "data": json.loads(json.dumps(result.data, default=str)),
             "files": result.files, "duration_ms": duration_ms},
        ]
        return result, blocks
