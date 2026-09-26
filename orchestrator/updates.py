"""Settings → Updates: compare this installation's VERSION with UPDATES.md on GitHub.

Only runs when the user presses "Check for updates" (the app never phones home on its own). It reads
one public file; nothing about the user or their chats is sent.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

import httpx

from inference.config import ROOT

UPDATES_URL = os.environ.get("GORUNRUN_UPDATES_URL",
                             "https://raw.githubusercontent.com/gorunrunai/local-ai/main/UPDATES.md")
INSTALL_COMMAND = "curl -fsSL https://raw.githubusercontent.com/gorunrunai/local-ai/main/install.sh | bash"
_HEADING = re.compile(r"^##\s+v?(\d+(?:\.\d+){1,2})(?:\s*\(([^)]*)\))?\s*$", re.MULTILINE)


@dataclass
class Release:
    version: str
    date: str | None
    notes: str            # Markdown


def current_version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def parse_updates(md: str) -> list[Release]:
    """The "## 1.2.0 (2026-10-01)" sections of UPDATES.md, newest first."""
    heads = list(_HEADING.finditer(md))
    out = []
    for i, m in enumerate(heads):
        body = md[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(md)]
        body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
        out.append(Release(m.group(1), (m.group(2) or "").strip() or None, body))
    return sorted(out, key=lambda r: parse_version(r.version), reverse=True)


def compare(current: str, md: str) -> dict:
    releases = parse_updates(md)
    if not releases:
        return {"ok": False, "current": current, "error": "The updates file on GitHub has no versions in it."}
    newer = [r for r in releases if parse_version(r.version) > parse_version(current)]
    return {"ok": True, "current": current, "latest": releases[0].version, "update_available": bool(newer),
            "releases": [asdict(r) for r in newer], "install_command": INSTALL_COMMAND}


async def check(url: str = UPDATES_URL, timeout: float = 10) -> dict:
    current = current_version()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"Cache-Control": "no-cache"})
    except httpx.HTTPError:
        return {"ok": False, "current": current,
                "error": "Couldn't reach GitHub. Check your internet connection and try again."}
    if r.status_code == 404:
        return {"ok": False, "current": current, "error": "Couldn't find the list of updates on GitHub. Try again later."}
    if r.status_code != 200:
        return {"ok": False, "current": current,
                "error": f"GitHub couldn't answer right now (error {r.status_code}). Try again later."}
    return compare(current, r.text)
