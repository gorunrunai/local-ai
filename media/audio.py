"""Audio processing: decode and transcribe (cached)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from media.cache import MediaCache
from media.ffmpeg import to_wav16k

if TYPE_CHECKING:
    from inference.services.stt import SpeechToText, Transcript

log = logging.getLogger(__name__)
TRANSCRIPT_VERSION = 1


async def transcribe_cached(src: Path, sha: str, stt: SpeechToText, cache: MediaCache,
                            duration_s: float | None, start: float | None = None,
                            end: float | None = None) -> tuple[Transcript, bool]:
    """Return (transcript, was_cached). Segment timestamps are absolute (offset by `start`)."""
    from inference.services.stt import Transcript

    params = {"v": TRANSCRIPT_VERSION, "stt": stt.spec.id, "start": start, "end": end}
    if hit := cache.get(sha, "transcript", params):
        return Transcript.from_dict(hit), True
    work = cache.scratch(sha)
    wav = await to_wav16k(src, work / "audio.wav", start, end)
    seg_duration = (end - (start or 0)) if end is not None else duration_s
    tr = await stt.transcribe(wav, duration_s=seg_duration)
    if start:
        for s in tr.segments:
            s.start = round(s.start + start, 2)
            s.end = round(s.end + start, 2)
    cache.put(sha, "transcript", params, tr.to_dict())
    wav.unlink(missing_ok=True)
    work.rmdir()
    return tr, False

