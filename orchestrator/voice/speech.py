"""Turning a streamed Markdown reply into speakable sentences."""

from __future__ import annotations

import re

_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CITE = re.compile(r"\s?\[\d+(?:\s*,\s*\d+)*\]")
_MATH = re.compile(r"\$\$?([^$]+)\$\$?")
_MARKUP = re.compile(r"(^|\s)[#>]+\s|[*_~|]+|^\s*[-+]\s+|^\s*\d+\.\s+", re.MULTILINE)
_BOUNDARY = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n{2,}|\n(?=\s*[-*+]\s|\s*\d+\.\s)")


def speakable(text: str) -> str:
    text = _FENCE.sub(" (code shown on screen) ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _CITE.sub("", text)
    text = _MATH.sub(" (formula shown on screen) ", text)
    text = _MARKUP.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    """Accumulates text deltas and releases speakable chunks as soon as sensible.

    The first chunk is released early (at a clause boundary once it has a few words) to
    cut time-to-first-audio; later chunks follow sentence boundaries. Code blocks are held
    back until they close, then summarized as "code shown on screen".
    """

    def __init__(self, first_min_chars: int = 24, min_chars: int = 40, max_chars: int = 260):
        self.buf = ""
        self.first = True
        self.first_min = first_min_chars
        self.min = min_chars
        self.max = max_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            # Inside an unclosed code block, only the text before it may be released.
            limit = self.buf.rfind("```") if self.buf.count("```") % 2 == 1 else len(self.buf)
            chunk = self._next_chunk(limit)
            if chunk is None:
                break
            if s := speakable(chunk):
                out.append(s)
                self.first = False
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf, ""
        s = speakable(rest)
        return [s] if s else []

    def _next_chunk(self, limit: int) -> str | None:
        need = self.first_min if self.first else self.min
        if limit < len(self.buf):  # an open code block follows: flush everything before it
            head = self.buf[:limit]
            ends = [m.end() for m in _BOUNDARY.finditer(head)]
            if head.strip() and ends and ends[-1] == len(head):
                chunk, self.buf = head, self.buf[limit:]
                return chunk
            return None
        for m in _BOUNDARY.finditer(self.buf):
            if m.end() >= need or m.group(0).startswith("\n"):
                chunk, self.buf = self.buf[: m.end()], self.buf[m.end():]
                return chunk
        if self.first and len(self.buf) >= 60:
            # No sentence end yet: break the first chunk at a comma/semicolon to start talking.
            cut = max(self.buf.rfind(", ", 0, 120), self.buf.rfind("; ", 0, 120))
            if cut >= self.first_min:
                chunk, self.buf = self.buf[: cut + 1], self.buf[cut + 2:]
                return chunk
        if len(self.buf) >= self.max:
            cut = self.buf.rfind(" ", 0, self.max)
            chunk, self.buf = self.buf[:cut], self.buf[cut + 1:]
            return chunk
        return None


# While the assistant is busy (talking, or running a tool such as a video render), what the user
# says decides what happens: a stop word cancels, an acknowledgement lets it carry on, anything else
# is a new request.
_STOP = re.compile(r"^(?:ok(?:ay)?,? )?(?:stop|cancel|cancel (?:it|that)|never ?mind|forget (?:it|that)|"
                   r"that's enough|enough|quiet|shut up|be quiet)[.!]*$", re.IGNORECASE)
_ACK = re.compile(r"^(?:ok(?:ay)?|yes|yeah|yep|yup|sure|thanks?|thank you|great|cool|nice|perfect|good|"
                  r"alright|all right|mm+[- ]?hmm+|uh[- ]?huh|hmm+|go ahead|sounds good|got it|i see|"
                  r"i'?m waiting|take your time|no rush|no problem|wow|awesome|right)\b", re.IGNORECASE)


def while_busy(text: str) -> str:
    """"stop", "ack" (acknowledgement: carry on) or "other" (a new request)."""
    t = text.strip().strip(".!").strip()
    if _STOP.match(t):
        return "stop"
    if "?" not in text and len(t.split()) <= 10 and _ACK.match(t):
        return "ack"
    return "other"
