"""Model wrappers for turn-taking: Silero VAD (per session) and Smart Turn (shared)."""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from orchestrator.voice.turns import FRAME_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)
SMART_TURN_REPO = "pipecat-ai/smart-turn-v3"
SMART_TURN_FILE = "smart-turn-v3.2-cpu.onnx"
SMART_TURN_MAX_S = 8


class SileroVAD:
    """Stateful per audio stream: create one per voice session."""

    def __init__(self):
        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        self._torch = torch
        self.model = load_silero_vad()

    def prob(self, frame: np.ndarray) -> float:
        assert frame.shape[0] == FRAME_SAMPLES
        with self._torch.no_grad():
            return float(self.model(self._torch.from_numpy(frame), SAMPLE_RATE).item())

    def reset(self) -> None:
        self.model.reset_states()


class SmartTurn:
    """Semantic end-of-turn model (Whisper-tiny encoder + classifier, ONNX, ~10 ms on CPU)."""

    def __init__(self):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import WhisperFeatureExtractor

        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 2
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        path = hf_hub_download(SMART_TURN_REPO, SMART_TURN_FILE, local_files_only=True)
        self.session = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        self.fe = WhisperFeatureExtractor(chunk_length=SMART_TURN_MAX_S)

    def complete_prob(self, audio: np.ndarray) -> float:
        audio = audio[-SMART_TURN_MAX_S * SAMPLE_RATE:]
        feats = self.fe(audio, sampling_rate=SAMPLE_RATE, return_tensors="np", padding="max_length",
                        max_length=SMART_TURN_MAX_S * SAMPLE_RATE, truncation=True, do_normalize=True)
        x = feats.input_features.squeeze(0).astype(np.float32)[None]
        return float(np.asarray(self.session.run(None, {"input_features": x})[0]).reshape(-1)[0])


@lru_cache(maxsize=1)
def smart_turn() -> SmartTurn | None:
    try:
        return SmartTurn()
    except Exception as e:  # noqa: BLE001 - fall back to silence-only turn ends
        log.warning("Smart Turn unavailable (%s); using silence thresholds only", e)
        return None


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
