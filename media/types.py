"""Types shared across the media pipeline."""

from __future__ import annotations

import hashlib
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal


class Kind(StrEnum):
    IMAGE = "image"
    SCREENSHOT = "screenshot"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


# Where an attachment came from; used for routing hints (e.g. pasted PNG => screenshot).
Source = Literal["upload", "paste", "screenshot", "camera", "voice_message", "dictation", "tool"]


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


@dataclass
class Attachment:
    path: Path
    filename: str = ""
    mime: str | None = None
    kind: Kind | None = None  # resolved by media.detect if None
    source: Source = "upload"
    id: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.filename = self.filename or self.path.name
        if not self.sha256:
            self.sha256 = sha256_file(self.path)
        self.id = self.id or self.sha256[:16]


@dataclass
class ContextBlock:
    """Text derived from an attachment (transcript, OCR, document text...).

    Rendered inside an <attachment_data> envelope: the system prompt tells the model that
    envelope contents are untrusted data, never instructions.
    """

    source: str
    type: Literal["transcript", "ocr", "frames", "document", "metadata", "note"]
    text: str
    attrs: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        attrs = {"name": self.source, "type": self.type, **self.attrs}
        attr_s = " ".join(f'{k}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items())
        # Neutralize any attempt to close the envelope from inside the data.
        body = self.text.replace("</attachment_data", "&lt;/attachment_data")
        return f"<attachment_data {attr_s}>\n{body}\n</attachment_data>"


@dataclass
class PreparedAttachment:
    attachment_id: str
    filename: str
    kind: Kind
    mode: Literal["native", "preprocessed", "text"]
    parts: list[dict] = field(default_factory=list)  # canonical content parts (inference.types)
    context: list[ContextBlock] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cached: bool = False


@dataclass
class Progress:
    attachment_id: str
    stage: str
    fraction: float
    message: str = ""


ProgressFn = Callable[[Progress], Awaitable[None]]


async def no_progress(_: Progress) -> None:
    return None
