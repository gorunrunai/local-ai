"""Tool-call parsing: native deltas first, JSON-in-text fallback, schema validation + repair."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

import json_repair
from jsonschema import Draft202012Validator

# Markers some models use to emit tool calls inline in the text stream.
INLINE_MARKERS = ("<tool_call>", "<|tool_call", "[TOOL_CALLS]", "<function_call>")
_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)
_FENCED = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as produced by the model

    def to_openai(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.arguments}}


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def calls_from_deltas(slots: dict[int, dict]) -> list[ToolCall]:
    out = []
    for i in sorted(slots):
        s = slots[i]
        if s.get("name"):
            out.append(ToolCall(s.get("id") or new_call_id(), s["name"].strip(), s.get("arguments") or "{}"))
    return out


def _as_call(obj, known: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    fn = obj["function"] if isinstance(obj.get("function"), dict) else None
    name = (fn or {}).get("name") or obj.get("name") or obj.get("tool")
    if not name or name not in known:
        return None
    if fn is not None:
        args = fn.get("arguments", {})
    else:
        args = obj.get("arguments", obj.get("parameters", obj.get("args", obj.get("input", {}))))
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return ToolCall(new_call_id(), name, raw)


def _loads_lenient(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            return json_repair.loads(s)
        except Exception:  # noqa: BLE001
            return None


def calls_from_text(text: str, known: set[str]) -> tuple[list[ToolCall], str]:
    """Find tool calls written as text. Returns (calls, text with the calls removed)."""
    calls: list[ToolCall] = []
    cleaned = text
    for m in _TAGGED.finditer(text):
        obj = _loads_lenient(m.group(1))
        for item in obj if isinstance(obj, list) else [obj]:
            if c := _as_call(item, known):
                calls.append(c)
        cleaned = cleaned.replace(m.group(0), "")
    if not calls:
        for m in _FENCED.finditer(text):
            if c := _as_call(_loads_lenient(m.group(1)), known):
                calls.append(c)
                cleaned = cleaned.replace(m.group(0), "")
    if not calls:
        stripped = text.strip()
        if (stripped.startswith("{") and stripped.endswith("}")
                and (c := _as_call(_loads_lenient(stripped), known))):
            calls.append(c)
            cleaned = ""
    return calls, cleaned.strip()


def parse_arguments(raw: str, schema: dict) -> tuple[dict | None, str | None]:
    """Decode + validate arguments. Returns (args, None) or (None, error for the model)."""
    raw = (raw or "").strip() or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        args = _loads_lenient(raw)
        if args is None:
            return None, "arguments are not valid JSON"
    if not isinstance(args, dict):
        return None, "arguments must be a JSON object"
    errors = sorted(Draft202012Validator(schema or {"type": "object"}).iter_errors(args),
                    key=lambda e: list(e.path))
    if errors:
        msgs = []
        for e in errors[:5]:
            where = "/".join(str(p) for p in e.path) or "(root)"
            msgs.append(f"{where}: {e.message}")
        return None, "; ".join(msgs)
    return args, None


class InlineCallFilter:
    """Hides inline tool-call markup from the live text stream.

    Text after a marker is withheld (it will be parsed as a tool call); a short tail that
    could be the start of a marker is held back until the next delta disambiguates it.
    """

    def __init__(self):
        self.pending = ""
        self.suppressing = False

    def feed(self, s: str) -> str:
        if self.suppressing:
            return ""
        buf = self.pending + s
        for marker in INLINE_MARKERS:
            idx = buf.find(marker)
            if idx != -1:
                self.suppressing = True
                self.pending = ""
                return buf[:idx]
        hold = 0
        for marker in INLINE_MARKERS:
            for k in range(min(len(marker) - 1, len(buf)), 0, -1):
                if buf.endswith(marker[:k]):
                    hold = max(hold, k)
                    break
        self.pending = buf[len(buf) - hold:] if hold else ""
        return buf[: len(buf) - hold] if hold else buf

    def flush(self) -> str:
        out, self.pending = ("" if self.suppressing else self.pending), ""
        return out


_CITE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")


def cited_indices(text: str) -> list[int]:
    out = []
    for m in _CITE.finditer(text):
        for part in re.split(r"\s*,\s*", m.group(1)):
            if re.fullmatch(r"\d+\s*[–-]\s*\d+", part):
                a, b = (int(x) for x in re.split(r"\s*[–-]\s*", part))
                out.extend(range(a, min(b, a + 20) + 1))
            elif part.isdigit():
                out.append(int(part))
    return out
