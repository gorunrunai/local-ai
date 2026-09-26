"""Token counting with the served model's own tokenizer (falls back to a char estimate)."""

from __future__ import annotations

import logging
from functools import lru_cache

from inference.config import LLMSpec

log = logging.getLogger(__name__)
MESSAGE_OVERHEAD = 6  # role markers / separators per chat message


@lru_cache(maxsize=8)
def _tokenizer(repo: str):
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        return Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json", local_files_only=True))
    except Exception as e:  # noqa: BLE001
        log.warning("tokenizer for %s unavailable (%s); estimating by characters", repo, e)
        return None


class TokenCounter:
    def __init__(self, spec: LLMSpec):
        self.spec = spec
        self.tok = _tokenizer(spec.repo)

    def text(self, s: str) -> int:
        if not s:
            return 0
        if self.tok is not None:
            return len(self.tok.encode(s, add_special_tokens=False).ids)
        return max(1, len(s) // 3)

    def part(self, p: dict) -> int:
        kind = p.get("type")
        if kind == "text":
            return self.text(p.get("text", ""))
        if kind == "image_url":
            return self.spec.limits.image_tokens
        if kind == "input_audio":
            return 25 * int(self.spec.limits.audio_seconds or 30)  # ~25 tokens/s of audio
        if kind == "input_video":
            return self.spec.limits.image_tokens * 16
        return 0

    def content(self, content) -> int:
        if isinstance(content, str):
            return self.text(content)
        return sum(self.part(p) for p in content or [])

    def message(self, m: dict) -> int:
        n = MESSAGE_OVERHEAD + self.content(m.get("content"))
        for tc in m.get("tool_calls") or []:
            n += self.text(tc.get("function", {}).get("name", "")) + self.text(
                tc.get("function", {}).get("arguments", ""))
        return n

    def messages(self, ms: list[dict]) -> int:
        return sum(self.message(m) for m in ms)
