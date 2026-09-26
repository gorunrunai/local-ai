"""Content-addressed local file store for uploads (data/files/<sha[:2]>/<sha><ext>)."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 2 GB: long screen recordings


class FileTooLarge(ValueError):
    pass


class FileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def save_upload(self, upload: UploadFile) -> tuple[Path, str, int]:
        """Stream an upload to disk while hashing; dedupe by content. Returns (path, sha, size)."""
        h = hashlib.sha256()
        size = 0
        ext = Path(upload.filename or "").suffix.lower()[:12]
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False, prefix=".up-") as tmp:
            while chunk := await upload.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    tmp.close()
                    Path(tmp.name).unlink(missing_ok=True)
                    raise FileTooLarge(f"file exceeds {MAX_UPLOAD_BYTES // 1024 ** 3} GB")
                h.update(chunk)
                tmp.write(chunk)
        sha = h.hexdigest()
        dest = self.root / sha[:2] / f"{sha}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            Path(tmp.name).unlink(missing_ok=True)
        else:
            shutil.move(tmp.name, dest)
        return dest, sha, size

    def save_bytes(self, data: bytes, filename: str) -> tuple[Path, str, int]:
        sha = hashlib.sha256(data).hexdigest()
        dest = self.root / sha[:2] / f"{sha}{Path(filename).suffix.lower()[:12]}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)
        return dest, sha, len(data)

    @staticmethod
    def guess_mime(filename: str, fallback: str | None = None) -> str | None:
        return fallback or mimetypes.guess_type(filename)[0]
