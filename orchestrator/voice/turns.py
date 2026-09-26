"""Turn-taking: voice activity detection, end-of-turn decision and barge-in.

Pure state machine over per-frame speech probabilities (Silero) plus an optional
"is the turn complete?" score (Smart Turn), so it is unit-testable without models.

Frames are 512 samples at 16 kHz (32 ms). Rules:
  * speech starts after `start_frames` consecutive frames above `start_threshold`;
  * after speech, a pause of `short_silence_ms` ends the turn if Smart Turn says the
    utterance is complete; a pause of `long_silence_ms` ends it regardless;
  * while the assistant is speaking, starting a new turn needs stronger evidence
    (`barge_threshold` for `barge_frames` frames), because echo of our own voice can
    leak through the microphone even with echo cancellation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

FRAME_SAMPLES = 512
SAMPLE_RATE = 16000
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 32 ms


class TurnEvent(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_CONTINUE = "speech_continue"
    PAUSE = "pause"                 # speech paused; caller may ask Smart Turn
    TURN_END = "turn_end"
    BARGE_IN = "barge_in"


@dataclass
class TurnConfig:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    start_frames: int = 2           # 64 ms of speech to start a turn
    barge_threshold: float = 0.8
    barge_frames: int = 8           # 256 ms of confident speech to interrupt
    short_silence_ms: float = 256   # end turn here if Smart Turn says "complete"
    long_silence_ms: float = 900    # end turn here regardless
    min_speech_ms: float = 200      # ignore blips shorter than this
    preroll_ms: float = 320         # audio kept from before speech start
    max_turn_s: float = 60

    @classmethod
    def from_sensitivity(cls, sensitivity: float) -> TurnConfig:
        """sensitivity 0..1 (UI slider): higher = reacts to quieter speech, ends turns sooner."""
        s = min(1.0, max(0.0, sensitivity))
        return cls(start_threshold=0.7 - 0.4 * s, end_threshold=0.5 - 0.3 * s,
                   short_silence_ms=352 - 192 * s, long_silence_ms=1200 - 600 * s)


@dataclass
class TurnState:
    in_speech: bool = False
    speech_frames: int = 0
    silence_frames: int = 0
    run: int = 0                     # consecutive frames above the start threshold
    pause_reported: bool = False
    frames: list = field(default_factory=list)   # audio frames of the current utterance
    preroll: deque = field(default_factory=lambda: deque(maxlen=10))


class TurnTracker:
    def __init__(self, config: TurnConfig | None = None):
        self.cfg = config or TurnConfig()
        self.s = TurnState()
        self.s.preroll = deque(maxlen=max(1, int(self.cfg.preroll_ms / FRAME_MS)))

    def reset(self) -> None:
        preroll_len = self.s.preroll.maxlen
        self.s = TurnState()
        self.s.preroll = deque(maxlen=preroll_len)

    @property
    def utterance_ms(self) -> float:
        return len(self.s.frames) * FRAME_MS

    def feed(self, frame, prob: float, assistant_speaking: bool = False) -> TurnEvent | None:
        """Consume one 32 ms frame and its speech probability; return an event or None."""
        c, s = self.cfg, self.s
        if not s.in_speech:
            s.preroll.append(frame)
            threshold, needed = ((c.barge_threshold, c.barge_frames) if assistant_speaking
                                 else (c.start_threshold, c.start_frames))
            s.run = s.run + 1 if prob >= threshold else 0
            if s.run >= needed:
                s.in_speech = True
                s.frames = list(s.preroll)
                s.speech_frames = s.run
                s.silence_frames = 0
                s.pause_reported = False
                return TurnEvent.BARGE_IN if assistant_speaking else TurnEvent.SPEECH_START
            return None

        s.frames.append(frame)
        if prob >= c.end_threshold:
            s.speech_frames += 1
            s.silence_frames = 0
            s.pause_reported = False
            if self.utterance_ms >= c.max_turn_s * 1000:
                return TurnEvent.TURN_END
            return TurnEvent.SPEECH_CONTINUE
        s.silence_frames += 1
        silence_ms = s.silence_frames * FRAME_MS
        if silence_ms >= c.long_silence_ms:
            if s.speech_frames * FRAME_MS < c.min_speech_ms:
                self.reset()  # a cough or click, not a turn
                return None
            return TurnEvent.TURN_END
        if silence_ms >= c.short_silence_ms and not s.pause_reported:
            s.pause_reported = True
            return TurnEvent.PAUSE
        return None

    def speech_end_offset_ms(self) -> float:
        """How long ago (in audio time) the user actually stopped speaking."""
        return self.s.silence_frames * FRAME_MS
