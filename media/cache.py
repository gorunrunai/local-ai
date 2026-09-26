"""Content-addressed cache for preprocessing results.

Layout: <root>/<sha256[:2]>/<sha256>/<stage>-<params digest>/{result.json, files...}
Re-asking about the same file (even renamed) hits the cache because keys use the content
hash; changing processing params (frame budget, max side...) produces a new entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inference.config import ROOT

DEFAULT_ROOT = ROOT / "data" / "cache" / "media"


def _digest(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]


class MediaCache:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)

    def entry_dir(self, sha: str, stage: str, params: dict[str, Any] | None = None) -> Path:
        return self.root / sha[:2] / sha / f"{stage}-{_digest(params or {})}"

    def get(self, sha: str, stage: str, params: dict[str, Any] | None = None) -> dict | None:
        f = self.entry_dir(sha, stage, params) / "result.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, sha: str, stage: str, params: dict[str, Any] | None, result: dict,
            files_from: Path | None = None) -> Path:
        """Atomically store `result` (and optionally move a directory of produced files)."""
        dest = self.entry_dir(sha, stage, params)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".tmp-"))
        try:
            if files_from is not None:
                for p in Path(files_from).iterdir():
                    shutil.move(str(p), tmp / p.name)
            (tmp / "result.json").write_text(json.dumps(result, default=str))
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return dest

    def scratch(self, sha: str) -> Path:
        """A fresh working dir for a processor; move results in with `put(files_from=...)`."""
        d = self.root / sha[:2] / sha
        d.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(dir=d, prefix=".work-"))

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
