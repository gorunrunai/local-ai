"""Lazily loaded in-process model services (STT, TTS, embeddings, reranker).

Each service loads on first use and unloads after `idle_unload_s` without calls. MLX work
from every service runs on one dedicated thread: MLX/Metal command submission is not
designed for concurrent use from several threads, and serializing keeps it predictable.
Torch (MPS) services get their own thread for the same reason.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from inference.config import ServiceSpec

log = logging.getLogger(__name__)
T = TypeVar("T")

MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
TORCH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="torch")
# MLX keeps freed buffers for reuse; unbounded, that cache grew the backend to ~15 GB after
# a few transcriptions. 512 MB keeps reuse benefits with a flat footprint (~3 GB total).
MLX_CACHE_LIMIT_BYTES = 512 * 1024 ** 2
_mlx_configured = False


def configure_mlx() -> None:
    global _mlx_configured
    if not _mlx_configured:
        import mlx.core as mx

        mx.set_cache_limit(MLX_CACHE_LIMIT_BYTES)
        _mlx_configured = True


def free_mlx_cache() -> None:
    try:
        import mlx.core as mx

        mx.clear_cache()
    except Exception:  # noqa: BLE001, S110 - best effort
        pass


class LazyService:
    kind = "service"
    executor = MLX_EXECUTOR

    def __init__(self, spec: ServiceSpec):
        self.spec = spec
        self._model: Any = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self.load_seconds: float | None = None

    # --- subclass hooks -------------------------------------------------------------
    def _load(self) -> Any:  # runs on the executor thread
        raise NotImplementedError

    def _unload(self) -> None:  # runs on the executor thread
        self._model = None
        gc.collect()
        free_mlx_cache()

    # --- public API -----------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def ensure_loaded(self) -> Any:
        async with self._lock:
            if self._model is None:
                t0 = time.perf_counter()
                log.info("loading %s %s", self.kind, self.spec.id)
                self._model = await self._submit(self._load)
                self.load_seconds = round(time.perf_counter() - t0, 2)
            self._last_used = time.monotonic()
            return self._model

    async def run(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Run `fn(model, *args)` on the service's executor, loading first if needed."""
        model = await self.ensure_loaded()
        try:
            return await self._submit(fn, model, *args, **kwargs)
        finally:
            self._last_used = time.monotonic()

    async def unload(self) -> None:
        async with self._lock:
            if self._model is not None:
                log.info("unloading %s %s", self.kind, self.spec.id)
                await self._submit(self._unload)

    async def maybe_unload_idle(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        if self.loaded and self.spec.idle_unload_s and now - self._last_used > self.spec.idle_unload_s:
            await self.unload()
            return True
        return False

    def status(self) -> dict:
        return {
            "id": self.spec.id,
            "kind": self.kind,
            "engine": self.spec.engine,
            "loaded": self.loaded,
            "est_memory_gb": self.spec.est_memory_gb,
            "idle_s": round(time.monotonic() - self._last_used, 1) if self.loaded else None,
            "load_seconds": self.load_seconds,
        }

    async def _submit(self, fn: Callable[..., T], *args, **kwargs) -> T:
        if self.executor is MLX_EXECUTOR:
            configure_mlx()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, lambda: fn(*args, **kwargs))
