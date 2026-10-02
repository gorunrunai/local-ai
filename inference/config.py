"""Typed view of config/models.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr

ROOT = Path(__file__).resolve().parent.parent
# Models this app converts itself (Wan 2.2 to MLX) live next to the Hugging Face cache, outside the
# program folder, so uninstalling or reinstalling doesn't throw away a 23 GB conversion.
MODEL_CACHE = Path(os.environ.get("GORUNRUN_MODEL_CACHE", "~/.cache/gorunrun/models")).expanduser()
CONFIG_PATH = ROOT / "config" / "models.yaml"


class Capabilities(BaseModel):
    image: bool = False
    audio: bool = False
    video: bool = False
    tools: bool = False
    thinking: bool = False


class MediaLimits(BaseModel):
    audio_seconds: float = 0
    video_seconds: float = 0
    video_frames: int = 32
    images_per_message: int = 8
    image_max_side: int = 1024
    video_frame_max_side: int | None = None  # defaults to image_max_side
    image_tokens: int = 500  # rough per-image prompt cost, for context budgeting


class LLMSpec(BaseModel):
    id: str = ""
    display_name: str
    repo: str                                  # MLX weights on Hugging Face, served by mlx-vlm
    est_memory_gb: float
    context_length: int = 32768
    ttl_s: int = 0
    thinking_budget: int | None = None
    prefix_cache_gb: float = 2.0   # mlx-vlm automatic prefix cache (RAM only); 0 disables
    capabilities: Capabilities = Field(default_factory=Capabilities)
    limits: MediaLimits = Field(default_factory=MediaLimits)


class ServiceSpec(BaseModel):
    id: str = ""
    engine: str
    repo: str
    est_memory_gb: float
    idle_unload_s: int = 600
    languages: list[str] = Field(default_factory=list)
    default_voice: str | None = None
    sample_rate: int | None = None
    dims: int | None = None
    device: str | None = None  # torch services: "mps" | "cpu" (None = auto)


class VideoSpec(BaseModel):
    """A local video-generation model, run as a subprocess in the separate `videogen/` venv."""
    id: str = ""
    display_name: str
    engine: Literal["ltx-2-mlx", "mlx-video-wan"]
    repo: str                                  # Hugging Face weights
    files: list[str] | None = None             # allow_patterns for the download (None = whole repo)
    extra_repos: list[tuple[str, list[str] | None]] = Field(default_factory=list)  # text encoder, tokenizer
    text_encoder: str | None = None            # LTX: Gemma repo used for prompt encoding
    convert_to: str | None = None              # Wan: MLX copy made at download, relative to MODEL_CACHE
    est_memory_gb: float                       # peak while rendering a clip of `est_seconds`
    est_seconds: float = 4
    memory_gb_per_s: float = 0.0               # extra peak memory per second beyond `est_seconds`
    est_render_s: float | None = None          # render time for a clip of `est_seconds`
    render_s_per_s: float | None = None        # extra render time per second beyond `est_seconds`
    fps: int = 24
    frame_step: int = 8                        # valid frame counts are 1 + k * frame_step
    max_seconds: float = 8                     # longest clip; Settings can change it (video_max_seconds)
    tested_seconds: float | None = None        # longest clip length tested for quality
    settable_max_seconds: float | None = None  # how far Settings may raise max_seconds (None = no higher)
    steps: int | None = None                   # None = engine default
    sizes: dict[str, tuple[int, int]] = Field(default_factory=dict)  # aspect -> (width, height)
    audio: bool = False                        # generates a synchronized soundtrack
    image_input: bool = True                   # can animate a still image
    _default_max_seconds: float = PrivateAttr(default=0.0)

    def model_post_init(self, context, /) -> None:
        self._default_max_seconds = self.max_seconds

    @property
    def default_max_seconds(self) -> float:
        """max_seconds as configured, before any Settings change."""
        return self._default_max_seconds

    @property
    def length_ceiling(self) -> float:
        """The longest max_seconds Settings may choose."""
        return max(self.settable_max_seconds or 0, self._default_max_seconds)

    def memory_gb(self, seconds: float) -> float:
        """Estimated peak memory for a clip of this length."""
        return round(self.est_memory_gb + self.memory_gb_per_s * max(0.0, seconds - self.est_seconds), 1)

    def planned_gb(self, seconds: float) -> float:
        """Memory to make room for before rendering. Up to the configured length this stays
        est_memory_gb, as it always has (macOS absorbs the rest of the peak, and the chat model stays
        loaded); longer clips, allowed in Settings, add the measured growth on top."""
        return round(self.est_memory_gb + self.memory_gb_per_s * max(0.0, seconds - self._default_max_seconds), 1)

    def render_s(self, seconds: float) -> float | None:
        """Estimated render time for a clip of this length (None when unknown)."""
        if self.est_render_s is None:
            return None
        return round(self.est_render_s + (self.render_s_per_s or 0) * max(0.0, seconds - self.est_seconds))


class ProxySpec(BaseModel):
    listen: str = "127.0.0.1:8090"
    binary: str = "bin/llama-swap"
    health_timeout_s: int = 300

    @property
    def base_url(self) -> str:
        return f"http://{self.listen}"


class Defaults(BaseModel):
    llm: str
    audio_listener: str | None = None
    title_llm: str | None = None
    stt: str
    tts: str
    embeddings: str
    reranker: str | None = None
    video: str | None = None


class Concurrency(BaseModel):
    sets: dict[str, str] = Field(default_factory=dict)
    evict_costs: dict[str, int] = Field(default_factory=dict)

    def partners(self, model_id: str) -> set[str]:
        """Models allowed to stay loaded alongside `model_id` (union over its sets)."""
        out: set[str] = set()
        for expr in self.sets.values():
            # Only simple "a & b & c" sets are used here; `|` alternatives count as separate sets.
            for alt in expr.split("|"):
                members = {m.strip(" ()") for m in alt.split("&")}
                if model_id in members:
                    out |= members - {model_id}
        return out


class ModelsConfig(BaseModel):
    memory_budget_gb: float = 45
    proxy: ProxySpec = Field(default_factory=ProxySpec)
    defaults: Defaults
    concurrency: Concurrency = Field(default_factory=Concurrency)
    llms: dict[str, LLMSpec]
    stt: dict[str, ServiceSpec] = Field(default_factory=dict)
    tts: dict[str, ServiceSpec] = Field(default_factory=dict)
    embeddings: dict[str, ServiceSpec] = Field(default_factory=dict)
    rerankers: dict[str, ServiceSpec] = Field(default_factory=dict)
    video: dict[str, VideoSpec] = Field(default_factory=dict)
    # Exact Hugging Face commit for every repo the installer downloads, so every install gets
    # the weights that were tested. Bump deliberately, after testing a newer upload.
    revisions: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, _ctx) -> None:
        for group in (self.llms, self.stt, self.tts, self.embeddings, self.rerankers, self.video):
            for key, spec in group.items():
                spec.id = key
        if self.proxy.listen.split(":")[0] not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("proxy.listen must be a loopback address")

    def llm(self, model_id: str | None = None) -> LLMSpec:
        key = model_id or self.defaults.llm
        if key not in self.llms:
            raise KeyError(f"unknown model {key!r}; known: {sorted(self.llms)}")
        return self.llms[key]


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | str = CONFIG_PATH) -> ModelsConfig:
    """models.yaml, with models.local.yaml (per-machine choices, not committed) merged over it."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    local = path.with_name(path.stem + ".local.yaml")
    if local.exists():
        data = _merge(data, yaml.safe_load(local.read_text()) or {})
    return ModelsConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_config() -> ModelsConfig:
    return load_config()
