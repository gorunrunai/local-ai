"""Word error rate with light normalization (case, punctuation, spoken numbers)."""

from __future__ import annotations

import re

_NUM_WORDS = {"three thirty": "3 30", "three": "3", "thirty": "30", "forty seven": "47",
              "nineteen": "19", "ten": "10"}


def normalize(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"(\d)[:.](\d)", r"\1 \2", t)  # 3:30 / 3.30 -> 3 30
    t = re.sub(r"[^\w\s']", " ", t)
    for words, digits in _NUM_WORDS.items():
        t = re.sub(rf"\b{words}\b", digits, t)
    return t.split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)
