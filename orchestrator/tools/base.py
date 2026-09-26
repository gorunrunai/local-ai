"""Tool interface, per-turn context and the citation source registry."""

from __future__ import annotations

import asyncio
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inference.manager import ModelManager
    from media.router import ModalityRouter
    from orchestrator.rag import MemoryService, Retriever
    from orchestrator.storage.files import FileStore
    from orchestrator.storage.store import Store


@dataclass
class Source:
    index: int
    title: str
    url: str | None = None
    attachment_id: str | None = None
    conversation_id: str | None = None
    locator: str | None = None   # "page 3", "00:42", ...
    snippet: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


class SourceRegistry:
    """Numbers every citable source seen in a turn; the model cites them as [n]."""

    def __init__(self, start: int = 1):
        self._next = start
        self.sources: dict[int, Source] = {}
        self._keys: dict[tuple, int] = {}

    def add(self, title: str, *, url: str | None = None, attachment_id: str | None = None,
            conversation_id: str | None = None, locator: str | None = None, snippet: str = "") -> Source:
        key = (url, attachment_id, conversation_id, locator, title if not (url or attachment_id) else None)
        if key in self._keys:
            return self.sources[self._keys[key]]
        src = Source(self._next, title, url, attachment_id, conversation_id, locator, snippet[:300])
        self.sources[src.index] = src
        self._keys[key] = src.index
        self._next += 1
        return src

    def get(self, n: int) -> Source | None:
        return self.sources.get(n)


def render_source(src: Source, body: str) -> str:
    attrs = [f'id="{src.index}"', f'title="{html.escape(src.title, quote=True)}"']
    if src.url:
        attrs.append(f'url="{html.escape(src.url, quote=True)}"')
    if src.locator:
        attrs.append(f'at="{html.escape(src.locator, quote=True)}"')
    return f"<source {' '.join(attrs)}>\n{body}\n</source>"


Emit = Callable[[str, dict], Awaitable[None]]


@dataclass
class ToolContext:
    conversation_id: str
    message_id: str
    project_id: str | None
    incognito: bool
    store: Store
    retriever: Retriever
    memory: MemoryService | None
    router: ModalityRouter
    manager: ModelManager
    files: FileStore
    sources: SourceRegistry
    emit: Emit
    model_id: str
    workdir_root: Any  # Path for code_exec per-conversation working dirs
    cancel: asyncio.Event | None = None  # set when the user presses Stop
    call_id: str = ""                    # id of the tool call being run (for progress events)


@dataclass
class ToolResult:
    content: str                                 # what the model sees (wrapped as untrusted data)
    ok: bool = True
    data: dict = field(default_factory=dict)     # structured result for the UI card
    images: list[str] = field(default_factory=list)  # image paths to show the model after the call
    files: list[dict] = field(default_factory=list)  # produced files (attachment records)


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    side_effect: bool = False      # changes something outside this chat -> confirm by default
    network: bool = False          # talks to the internet
    uses_memory: bool = False      # disabled in incognito
    cross_conversation: bool = False  # reads other chats -> disabled in incognito

    def available(self) -> bool:
        """False when this installation can't run the tool (it's then not offered at all)."""
        return True

    def definition(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


def wrap_tool_output(name: str, text: str, ok: bool = True) -> str:
    """Tool output is data: the envelope matches the system prompt's untrusted-data rule."""
    body = text.replace("</tool_output", "&lt;/tool_output")
    status = "ok" if ok else "error"
    return f'<tool_output name="{name}" status="{status}" trust="untrusted">\n{body}\n</tool_output>'
