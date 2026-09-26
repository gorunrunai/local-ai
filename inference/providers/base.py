"""The ModelProvider interface every inference backend implements."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from inference.types import ChatRequest, StreamEvent


@runtime_checkable
class ModelProvider(Protocol):
    """Streams normalized events for an OpenAI-style chat request.

    Implementations translate the canonical message format in `inference.types` into
    whatever their backend expects and translate the backend's stream back into
    `StreamEvent`s, so the orchestrator never sees backend-specific field names.
    """

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...

    async def list_models(self) -> list[str]: ...

    async def aclose(self) -> None: ...
