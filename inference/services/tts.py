"""Text-to-speech service (Kokoro via mlx-audio), with sentence-level streaming."""

from __future__ import annotations

import asyncio
import io
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from inference.services.base import LazyService

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


@dataclass
class AudioChunk:
    samples: np.ndarray  # float32 mono
    sample_rate: int
    text: str
    elapsed_s: float

    def wav_bytes(self) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, self.samples, self.sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def pcm16(self) -> bytes:
        return (np.clip(self.samples, -1, 1) * 32767).astype("<i2").tobytes()


class TextToSpeech(LazyService):
    kind = "tts"

    def _load(self):
        if self.spec.engine != "mlx-audio":
            raise ValueError(f"unknown TTS engine {self.spec.engine}")
        from mlx_audio.tts.utils import load

        model = load(self.spec.repo)
        # First generate() builds the G2P pipeline (~5 s); do it at load, not on first reply.
        for _ in model.generate(text="Ready.", voice=self.spec.default_voice, lang_code="a"):
            pass
        return model

    def _synth_sync(self, model, text: str, voice: str | None, speed: float) -> AudioChunk:
        t0 = time.perf_counter()
        parts = [np.asarray(r.audio, dtype=np.float32)
                 for r in model.generate(text=text, voice=voice or self.spec.default_voice,
                                         speed=speed, lang_code=_lang_for(voice))]
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        sr = getattr(model, "sample_rate", None) or self.spec.sample_rate or 24000
        return AudioChunk(audio, sr, text, round(time.perf_counter() - t0, 3))

    async def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> AudioChunk:
        return await self.run(self._synth_sync, text, voice, speed)

    async def stream(self, text: str, voice: str | None = None, speed: float = 1.0,
                     cancel: asyncio.Event | None = None) -> AsyncIterator[AudioChunk]:
        """Synthesize sentence by sentence so playback can start after the first one."""
        for sentence in split_sentences(text):
            if cancel is not None and cancel.is_set():
                return
            yield await self.synthesize(sentence, voice, speed)


def _lang_for(voice: str | None) -> str:
    # Kokoro voice ids start with a language letter: a=US English, b=UK English, ...
    return (voice or "a")[0] if voice and voice[0] in "abefhijpz" else "a"
