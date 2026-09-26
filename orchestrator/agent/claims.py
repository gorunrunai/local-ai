"""Catch replies that claim an action no tool performed ("I've created a video…" with no video).

Models sometimes describe the result of a tool they never called, especially in voice mode. The agent
loop checks each final reply: if a sentence claims a finished action or something visible on screen,
and no successful call of a matching tool happened in this reply, the model gets one correction and
must either call the tool or tell the user it hasn't done it.
"""

from __future__ import annotations

import re

DONE = re.compile(
    r"\b(?:I(?:'ve| have| just|'ve just| have just| have now|'ve now)?)\s+(?:successfully\s+)?"
    r"(created|generated|made|rendered|drawn|drew|built|displayed|shown|showed|put|found|searched|"
    r"looked up|fetched|saved|animated|produced|opened)\b", re.IGNORECASE)
SHOWN = re.compile(
    r"\b(?:on (?:your|the) screen|in the side panel|(?:is|are|should be) (?:now )?"
    r"(?:playing|displayed|visible|showing on))\b", re.IGNORECASE)

# Work "in progress" across replies never exists: tools run to completion inside a reply, nothing
# continues afterwards, and the assistant can't come back on its own later.
PROGRESS = re.compile(
    r"\b(?:I'?m|I am)\s+(?:now\s+|still\s+|currently\s+)?(?:generating|creating|rendering|making|drawing|"
    r"searching|working on|building|preparing|processing)\b"
    r"|\b(?:is|are|it's|it is)\s+(?:still\s+|currently\s+|now\s+)?(?:rendering|generating|processing|"
    r"being (?:generated|rendered|created|made|processed))\b"
    r"|\b(?:should|will) be (?:ready|done|finished)\b|\bready (?:soon|shortly|in a (?:few|couple|minute|moment))"
    r"|\bI'?ll (?:let you know|notify you|tell you when|update you)|\bI will (?:let you know|notify you|update you)",
    re.IGNORECASE)

# What a sentence talks about -> the tools that could have done it.
KINDS: list[tuple[re.Pattern, set[str], bool]] = [       # (pattern, tools, only when nothing is in the text)
    (re.compile(r"\b(video|clip|animation|animated)\b", re.IGNORECASE), {"generate_video"}, False),
    (re.compile(r"\b(diagram|chart|graph|visuali[sz]ation|visual|artifact|side panel|web page|app)\b", re.IGNORECASE),
     {"create_artifact", "generate_video"}, True),
    (re.compile(r"\b(search(?:ed)?|looked up|online|on the web|the internet|photo|picture|image|article)\b", re.IGNORECASE),
     {"web_search", "web_fetch"}, False),
    (re.compile(r"\b(saved|remember(?:ed)?|memory)\b", re.IGNORECASE), {"memory_save"}, False),
]
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def unbacked_claim(text: str, succeeded: set[str], *, voice: bool) -> tuple[str, set[str], bool] | None:
    """The first sentence claiming an action that no successful tool call backs: (sentence, the tools it
    needed, whether it claims work in progress)."""
    # In typed chat a diagram or table may be right there in the reply (Markdown, Mermaid, code).
    shown_inline = not voice and "```" in text
    for sentence in _SENTENCES.split(text):
        progress = bool(PROGRESS.search(sentence))
        if not (progress or DONE.search(sentence) or SHOWN.search(sentence)):
            continue
        needed: set[str] = set()
        for pattern, tools, needs_nothing_inline in KINDS:
            if pattern.search(sentence) and not (needs_nothing_inline and shown_inline):
                needed |= tools
        if not needed and (progress or (voice and SHOWN.search(sentence))):
            # "It should be ready in a few minutes": about whatever the reply is about.
            for pattern, tools, _ in KINDS:
                if pattern.search(text):
                    needed |= tools
            needed = needed or {"create_artifact", "generate_video"}
        if needed and not (needed & succeeded):
            return sentence.strip(), needed, progress
    return None


def correction(sentence: str, tools: set[str], progress: bool = False) -> str:
    names = " or ".join(sorted(tools))
    if progress:
        return (f"[system note] Your reply says: \"{sentence}\" But nothing is running: no {names} call is in "
                "progress. Tools run to completion inside your reply, nothing continues in the background, and "
                "you can't come back to the user later on your own. Don't say it's in progress or promise to let "
                "them know. Now either call the tool to do it (it finishes before your reply ends), or tell the "
                "user plainly, in one short sentence, that it hasn't been started, and offer to start it.")
    return (f"[system note] Your reply says: \"{sentence}\" But no {names} call succeeded in this reply, so "
            "nothing was made, found or shown, and the user can't see anything. Don't claim it. Now either "
            "call the tool to actually do it, or tell the user plainly, in one short sentence, that you "
            "haven't done it yet (and why). If it was done in an earlier reply, say that instead.")
