"""Speech-to-text service (Parakeet via parakeet-mlx, or Whisper via mlx-whisper)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from inference.services.base import LazyService

LONG_AUDIO_CHUNK_S = 120.0  # parakeet chunking for long files (overlap handled by the lib)


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    duration_s: float | None = None
    engine: str = ""
    elapsed_s: float = 0.0

    @property
    def rtf(self) -> float | None:
        """Real-time factor: processing time / audio duration (lower is faster)."""
        if not self.duration_s:
            return None
        return round(self.elapsed_s / self.duration_s, 4)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rtf"] = self.rtf
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Transcript:
        d = {k: v for k, v in d.items() if k != "rtf"}
        d["segments"] = [Segment(**s) for s in d.get("segments", [])]
        return cls(**d)

    def timestamped(self) -> str:
        """Render as `[mm:ss] text` lines, the format injected into prompts."""
        return "\n".join(f"[{_ts(s.start)}] {s.text.strip()}"
                         for s in self.segments) or self.text


def _ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class SpeechToText(LazyService):
    kind = "stt"

    def _load(self):
        if self.spec.engine == "parakeet-mlx":
            from parakeet_mlx import from_pretrained

            return from_pretrained(self.spec.repo)
        if self.spec.engine == "mlx-whisper":
            return self.spec.repo  # mlx_whisper loads (and caches) by repo per call
        raise ValueError(f"unknown STT engine {self.spec.engine}")

    def _transcribe_sync(self, model, path: str, duration_s: float | None) -> Transcript:
        t0 = time.perf_counter()
        if self.spec.engine == "parakeet-mlx":
            chunk = LONG_AUDIO_CHUNK_S if (duration_s or 0) > LONG_AUDIO_CHUNK_S else None
            res = model.transcribe(path, chunk_duration=chunk)
            segs = [Segment(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in res.sentences]
            text, lang = res.text, "en"
        else:
            import mlx_whisper

            res = mlx_whisper.transcribe(path, path_or_hf_repo=model, word_timestamps=False)
            segs = [Segment(round(s["start"], 2), round(s["end"], 2), s["text"].strip())
                    for s in res.get("segments", [])]
            text, lang = res.get("text", "").strip(), res.get("language")
        return Transcript(text=text.strip(), segments=segs, language=lang, duration_s=duration_s,
                          engine=self.spec.id, elapsed_s=round(time.perf_counter() - t0, 3))

    async def transcribe_array(self, audio, sample_rate: int = 16000) -> Transcript:
        """Transcribe in-memory mono float32 audio (voice mode / dictation)."""
        import tempfile

        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            sf.write(f.name, audio, sample_rate, subtype="PCM_16")
            return await self.transcribe(f.name, duration_s=len(audio) / sample_rate)

    async def transcribe(self, path: str | Path, duration_s: float | None = None) -> Transcript:
        if duration_s is None:
            duration_s = audio_duration(path)
        return await self.run(self._transcribe_sync, str(path), duration_s)


def audio_duration(path: str | Path) -> float | None:
    try:
        import soundfile as sf

        return float(sf.info(str(path)).duration)
    except Exception:  # noqa: BLE001 - compressed formats: fall back to ffprobe
        import json
        import subprocess

        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                                  str(path)], capture_output=True, text=True, timeout=30, check=False).stdout
            return float(json.loads(out)["format"]["duration"])
        except Exception:  # noqa: BLE001
            return None
