"""Thin async wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise FFmpegError("ffmpeg/ffprobe not found on PATH; run `make setup` (brew install ffmpeg)")


async def run(*args: str, timeout: float = 600) -> tuple[bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise FFmpegError(f"{args[0]} timed out after {timeout}s")
    if proc.returncode != 0:
        raise FFmpegError(f"{args[0]} failed ({proc.returncode}): {err.decode(errors='replace')[-800:]}")
    return out, err


@dataclass
class MediaInfo:
    duration_s: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None


async def probe(path: Path) -> MediaInfo:
    out, _ = await run("ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                       "-show_streams", str(path), timeout=60)
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") != "0/0":
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else None
    return MediaInfo(duration_s=duration, has_video=video is not None, has_audio=audio is not None,
                     width=video.get("width") if video else None,
                     height=video.get("height") if video else None, fps=fps,
                     codec=(video or audio or {}).get("codec_name"))


async def to_wav16k(src: Path, dest: Path, start: float | None = None,
                    end: float | None = None) -> Path:
    """Decode any audio/video file to 16 kHz mono PCM WAV (optionally a time range)."""
    args = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if end is not None:
        args += ["-t", f"{max(0.0, end - (start or 0)):.3f}"]
    args += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    await run(*args)
    return dest
