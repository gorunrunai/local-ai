"""Backend-neutral request and stream-event types shared by providers and the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# OpenAI-style message dicts. Content may be a string or a list of parts:
#   {"type": "text", "text": ...}
#   {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
#   {"type": "input_audio", "input_audio": {"path": "/abs/file.wav"}}   # canonical, provider re-encodes
#   {"type": "input_video", "input_video": {"path": "/abs/file.mp4"}}   # canonical, provider re-encodes
Message = dict[str, Any]


@dataclass
class ChatRequest:
    model: str
    messages: list[Message]
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    thinking: bool = False
    thinking_budget: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextDelta:
    text: str
    kind: Literal["text"] = "text"


@dataclass
class ReasoningDelta:
    text: str
    kind: Literal["reasoning"] = "reasoning"


@dataclass
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""
    kind: Literal["tool_call"] = "tool_call"


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_s: float | None = None
    decode_tok_s: float | None = None
    kind: Literal["usage"] = "usage"


@dataclass
class Done:
    finish_reason: str | None = None
    kind: Literal["done"] = "done"


@dataclass
class StreamError:
    message: str
    status: int | None = None
    kind: Literal["error"] = "error"


StreamEvent = TextDelta | ReasoningDelta | ToolCallDelta | Usage | Done | StreamError
