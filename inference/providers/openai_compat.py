"""OpenAI-compatible provider: llama-swap in front of mlx-vlm servers.

mlx-vlm's wire format differs from OpenAI's in a few details, which this module handles:
  * video parts are `video_url` with a local path; audio is base64 `input_audio`.
  * thinking is toggled with a top-level `enable_thinking` (and optional `thinking_budget`).
  * reasoning deltas arrive as `reasoning` or `reasoning_content`.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from inference.types import (
    ChatRequest,
    Done,
    Message,
    ReasoningDelta,
    StreamError,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    Usage,
)


def _b64_file(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def encode_part(part: dict) -> dict:
    """Translate one canonical content part into mlx-vlm's wire format."""
    kind = part.get("type")
    if kind == "input_audio":
        audio = part["input_audio"]
        if "path" in audio:
            fmt = Path(audio["path"]).suffix.lstrip(".").lower() or "wav"
            return {"type": "input_audio", "input_audio": {"data": _b64_file(audio["path"]), "format": fmt}}
        return part
    if kind == "input_video":
        video = part["input_video"]
        if "path" not in video:
            return part
        return {"type": "video_url", "video_url": {"url": str(Path(video["path"]).resolve())}}
    return part


def _has_video(messages: list[Message]) -> bool:
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "input_video" for p in m["content"])
               for m in messages)


def fold_system_into_user(messages: list[Message]) -> list[Message]:
    """mlx-vlm places a native video on the first message; Qwen's template forbids media in a
    system message. For video requests, move the system prompt into the first user turn."""
    if not messages or messages[0].get("role") != "system":
        return messages
    sys_text = messages[0]["content"] if isinstance(messages[0]["content"], str) else " ".join(
        p.get("text", "") for p in messages[0]["content"])
    rest = list(messages[1:])
    for i, m in enumerate(rest):
        if m.get("role") == "user":
            c = m["content"]
            prefix = f"<system_instructions>\n{sys_text}\n</system_instructions>\n\n"
            rest[i] = {**m, "content": prefix + c if isinstance(c, str)
                       else [*c[:0], {"type": "text", "text": prefix}, *c]}
            return rest
    return messages


def encode_messages(messages: list[Message]) -> list[Message]:
    if _has_video(messages):
        messages = fold_system_into_user(messages)
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            m = {**m, "content": [encode_part(p) for p in content]}
        out.append(m)
    return out


def build_body(req: ChatRequest) -> dict:
    body: dict = {
        "model": req.model,
        "messages": encode_messages(req.messages),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    for key in ("temperature", "top_p", "max_tokens", "stop", "tool_choice"):
        value = getattr(req, key)
        if value is not None:
            body[key] = value
    if req.tools:
        body["tools"] = req.tools
    body["enable_thinking"] = req.thinking
    if req.thinking and req.thinking_budget:
        body["thinking_budget"] = req.thinking_budget
    body.update(req.extra)
    return body


def parse_sse_event(data: dict) -> list[StreamEvent]:
    """Convert one decoded SSE `data:` payload into normalized events."""
    events: list[StreamEvent] = []
    if err := data.get("error"):
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return [StreamError(message=msg or "upstream error")]
    for choice in data.get("choices") or []:
        delta = choice.get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            events.append(ReasoningDelta(reasoning))
        if content := delta.get("content"):
            events.append(TextDelta(content))
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, dict):  # some servers send parsed objects
                args = json.dumps(args)
            events.append(ToolCallDelta(index=tc.get("index", 0), id=tc.get("id"),
                                        name=fn.get("name"), arguments=args or ""))
        if choice.get("finish_reason"):
            events.append(Done(finish_reason=choice["finish_reason"]))
    if usage := data.get("usage"):
        events.append(Usage(prompt_tokens=usage.get("prompt_tokens"),
                            completion_tokens=usage.get("completion_tokens")))
    return events


class OpenAICompatProvider:
    def __init__(self, base_url: str, timeout: float = 900.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0),
                                         transport=transport)

    async def list_models(self) -> list[str]:
        r = await self._client.get(f"{self.base_url}/v1/models")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = build_body(request)
        t0 = time.perf_counter()
        first: float | None = None
        usage = Usage()
        done: Done | None = None
        try:
            async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                           json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode(errors="replace")[:1000]
                    yield StreamError(message=text or r.reason_phrase, status=r.status_code)
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ev in parse_sse_event(data):
                        if isinstance(ev, Usage):
                            usage.prompt_tokens = ev.prompt_tokens
                            usage.completion_tokens = ev.completion_tokens
                            continue
                        if isinstance(ev, Done):
                            done = ev
                            continue
                        if first is None and not isinstance(ev, StreamError):
                            first = time.perf_counter()
                        yield ev
        except httpx.HTTPError as e:
            yield StreamError(message=f"{type(e).__name__}: {e}")
            return
        end = time.perf_counter()
        usage.ttft_s = round((first or end) - t0, 3)
        if first and usage.completion_tokens and end > first:
            usage.decode_tok_s = round(usage.completion_tokens / (end - first), 1)
        yield usage
        yield done or Done(finish_reason="stop")

    async def aclose(self) -> None:
        await self._client.aclose()
