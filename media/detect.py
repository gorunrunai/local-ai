"""Classify an attachment into a media Kind from magic bytes, extension and source."""

from __future__ import annotations

from pathlib import Path

from media.types import Attachment, Kind

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".webm", ".ogg", ".oga", ".opus", ".flac", ".aac", ".caf"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".tsv", ".md", ".markdown", ".txt",
           ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".htm", ".rst", ".log"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift", ".go", ".rs", ".c", ".h",
            ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".zsh", ".sql", ".css", ".scss", ".lua",
            ".r", ".m", ".scala", ".dart", ".vue", ".svelte", ".ini", ".cfg", ".env", ".dockerfile"}
SCREENSHOT_HINTS = ("screenshot", "screen shot", "cleanshot", "capture", "snip")


def _magic_kind(head: bytes) -> Kind | None:
    if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff" or head[:4] == b"GIF8":
        return Kind.IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Kind.IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return Kind.AUDIO
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"hevc"):
            return Kind.IMAGE
        if brand in (b"M4A ", b"M4B "):
            return Kind.AUDIO
        return Kind.VIDEO  # mp4/mov/m4v; audio-only mp4s are refined via ffprobe
    if head[:4] == b"%PDF":
        return Kind.DOCUMENT
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return Kind.AUDIO
    if head[:4] in (b"OggS", b"fLaC"):
        return Kind.AUDIO
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return Kind.VIDEO  # webm/mkv; voice notes are refined via ffprobe (no video stream)
    return None


def detect_kind(att: Attachment) -> Kind:
    if att.kind:
        return att.kind
    ext = Path(att.filename).suffix.lower()
    with open(att.path, "rb") as f:
        head = f.read(32)
    kind = _magic_kind(head)
    if kind is None:
        mime = (att.mime or "").split(";")[0]
        if mime.startswith("image/"):
            kind = Kind.IMAGE
        elif mime.startswith("audio/"):
            kind = Kind.AUDIO
        elif mime.startswith("video/"):
            kind = Kind.VIDEO
        elif ext in IMAGE_EXT:
            kind = Kind.IMAGE
        elif ext in AUDIO_EXT:
            kind = Kind.AUDIO
        elif ext in VIDEO_EXT:
            kind = Kind.VIDEO
        elif ext in DOC_EXT or ext in CODE_EXT or (att.mime or "").startswith("text/"):
            kind = Kind.DOCUMENT
        else:
            kind = Kind.DOCUMENT if _looks_textual(att.path) else Kind.UNKNOWN
    # ZIP containers (docx/pptx/xlsx) have PK magic; route by extension.
    if head[:2] == b"PK" and ext in {".docx", ".pptx", ".xlsx", ".xlsm"}:
        kind = Kind.DOCUMENT
    if kind is Kind.AUDIO and att.source == "voice_message":
        return Kind.AUDIO
    if att.mime and att.mime.startswith("audio/") and kind is Kind.VIDEO:
        kind = Kind.AUDIO  # e.g. audio/webm from MediaRecorder
    if kind is Kind.IMAGE and is_screenshot(att, head):
        kind = Kind.SCREENSHOT
    return kind


def is_screenshot(att: Attachment, head: bytes) -> bool:
    if att.source in ("screenshot", "paste") and head.startswith(b"\x89PNG"):
        return True
    name = att.filename.lower()
    return head.startswith(b"\x89PNG") and any(h in name for h in SCREENSHOT_HINTS)


def _looks_textual(path: Path, sample: int = 4096) -> bool:
    with open(path, "rb") as f:
        chunk = f.read(sample)
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
