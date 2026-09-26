"""Deterministic stand-ins for models, so orchestrator logic is testable without weights."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import numpy as np

from inference.config import Capabilities, LLMSpec, MediaLimits
from inference.types import Done, ReasoningDelta, TextDelta, ToolCallDelta, Usage

DIMS = 64


def bow_vector(text: str) -> np.ndarray:
    """Hashed bag-of-words: texts sharing words get close vectors."""
    v = np.zeros(DIMS, dtype=np.float32)
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) > 2:
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIMS] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v + 1 / np.sqrt(DIMS)


class FakeEmbedder:
    @dataclass
    class spec:
        id: str = "fake-embed"
        dims: int = DIMS
        est_memory_gb: float = 0.0

    async def embed(self, texts, is_query=False):
        return np.stack([bow_vector(t) for t in texts]) if texts else np.zeros((0, DIMS), np.float32)


class ScriptedProvider:
    """Replays a list of turns; each turn is a list of StreamEvents (or a callable(request))."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat_stream(self, request):
        self.requests.append(request)
        turn = self.turns.pop(0) if self.turns else [TextDelta("(no more script)")]
        if callable(turn):
            turn = turn(request)
        for ev in turn:
            yield ev
        yield Usage(prompt_tokens=10, completion_tokens=5, ttft_s=0.01, decode_tok_s=100.0)
        yield Done("stop")

    async def aclose(self):
        pass


def text_turn(s: str):
    return [TextDelta(s)]


def tool_turn(name: str, args: str, call_id: str = "c1", text: str = ""):
    evs = [TextDelta(text)] if text else []
    return [*evs, ToolCallDelta(index=0, id=call_id, name=name, arguments=args)]


def thinking_turn(thought: str, answer: str):
    return [ReasoningDelta(thought), TextDelta(answer)]


def spec(**caps) -> LLMSpec:
    return LLMSpec(id="fake-llm", display_name="Fake", repo="fake/fake",
                   est_memory_gb=1, context_length=caps.pop("context_length", 8192),
                   capabilities=Capabilities(image=True, audio=False, video=True, tools=True, thinking=True),
                   limits=MediaLimits(image_tokens=100, image_max_side=256, video_frames=4))
