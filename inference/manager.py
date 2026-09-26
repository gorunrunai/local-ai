"""Model manager: lifecycle, memory budget and status for every model in the system.

LLMs run as llama-swap upstream processes (one at a time). STT/TTS/embeddings/reranker
are in-process lazy services. The manager enforces `memory_budget_gb` before loading
anything, evicting idle services first, and reports live footprint + speed per model.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from dataclasses import dataclass, field

import httpx
import psutil

from inference.config import ROOT, LLMSpec, ModelsConfig, get_config
from inference.memory import phys_footprint, system_memory, tree_footprint
from inference.providers.openai_compat import OpenAICompatProvider
from inference.services.base import LazyService
from inference.services.embeddings import Embedder, Reranker
from inference.services.stt import SpeechToText
from inference.services.tts import TextToSpeech
from inference.swap import write_swap_config
from inference.types import ChatRequest, Usage
from inference.video import VideoGenerator

log = logging.getLogger(__name__)
GB = 1024 ** 3


def llm_downloaded(spec: LLMSpec) -> bool:
    """Whether the model's weights are in the local Hugging Face cache (never hits the network)."""
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(spec.repo, "config.json"), str)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class SpeedStats:
    samples: collections.deque = field(default_factory=lambda: collections.deque(maxlen=20))

    def add(self, u: Usage) -> None:
        if u.decode_tok_s:
            self.samples.append((u.decode_tok_s, u.ttft_s or 0.0))

    def summary(self) -> dict:
        if not self.samples:
            return {"decode_tok_s": None, "ttft_s": None, "n": 0}
        n = len(self.samples)
        return {"decode_tok_s": round(sum(s[0] for s in self.samples) / n, 1),
                "ttft_s": round(sum(s[1] for s in self.samples) / n, 3), "n": n}


class ModelManager:
    def __init__(self, cfg: ModelsConfig | None = None, start_proxy: bool = True):
        self.cfg = cfg or get_config()
        self.start_proxy = start_proxy
        self.proxy_url = self.cfg.proxy.base_url
        self._proxy_proc: asyncio.subprocess.Process | None = None
        self._proxy_log = None
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.cfg.proxy.health_timeout_s, connect=5))
        self.provider = OpenAICompatProvider(self.proxy_url)
        self.speed: dict[str, SpeedStats] = collections.defaultdict(SpeedStats)
        self.services: dict[str, LazyService] = {}
        for sid, spec in self.cfg.stt.items():
            self.services[f"stt:{sid}"] = SpeechToText(spec)
        for sid, spec in self.cfg.tts.items():
            self.services[f"tts:{sid}"] = TextToSpeech(spec)
        for sid, spec in self.cfg.embeddings.items():
            self.services[f"embeddings:{sid}"] = Embedder(spec)
        for sid, spec in self.cfg.rerankers.items():
            self.services[f"reranker:{sid}"] = Reranker(spec)
        self._reaper: asyncio.Task | None = None
        self._llm_lock = asyncio.Lock()
        self.reserved_gb = 0.0  # held by a running video render (inference/video.py)
        self.video = VideoGenerator(self)

    # --- service accessors ------------------------------------------------------------
    def stt(self, sid: str | None = None) -> SpeechToText:
        return self.services[f"stt:{sid or self.cfg.defaults.stt}"]  # type: ignore[return-value]

    def tts(self, sid: str | None = None) -> TextToSpeech:
        return self.services[f"tts:{sid or self.cfg.defaults.tts}"]  # type: ignore[return-value]

    def embedder(self, sid: str | None = None) -> Embedder:
        return self.services[f"embeddings:{sid or self.cfg.defaults.embeddings}"]  # type: ignore[return-value]

    def reranker(self, sid: str | None = None) -> Reranker | None:
        key = sid or self.cfg.defaults.reranker
        return self.services.get(f"reranker:{key}") if key else None  # type: ignore[return-value]

    def llm_spec(self, model_id: str | None = None) -> LLMSpec:
        return self.cfg.llm(model_id)

    # --- lifecycle ----------------------------------------------------------------------
    async def start(self) -> None:
        if self.start_proxy:
            await self._start_proxy()
        self._reaper = asyncio.create_task(self._reap_loop())

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for svc in self.services.values():
            await svc.unload()
        if self._proxy_proc and self._proxy_proc.returncode is None:
            try:
                await self._http.post(f"{self.proxy_url}/api/models/unload", timeout=30)
            except httpx.HTTPError:
                pass
            self._proxy_proc.terminate()
            try:
                await asyncio.wait_for(self._proxy_proc.wait(), 15)
            except TimeoutError:
                self._proxy_proc.kill()
        if self._proxy_log:
            self._proxy_log.close()
        await self.provider.aclose()
        await self._http.aclose()

    async def _start_proxy(self) -> None:
        if await self._proxy_healthy():
            log.info("llama-swap already running at %s; reusing it", self.proxy_url)
            return
        cfg_path = write_swap_config(self.cfg)
        binary = ROOT / self.cfg.proxy.binary
        if not binary.exists():
            raise FileNotFoundError(f"{binary} missing; run `make setup`")
        log_path = ROOT / "data" / "logs" / "llama-swap.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        self._proxy_log = log_path.open("ab")
        self._proxy_proc = await asyncio.create_subprocess_exec(
            str(binary), "-config", str(cfg_path), "-listen", self.cfg.proxy.listen,
            stdout=self._proxy_log, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(ROOT))
        for _ in range(100):
            if await self._proxy_healthy():
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"llama-swap did not become healthy; see {log_path}")

    async def _proxy_healthy(self) -> bool:
        try:
            return (await self._http.get(f"{self.proxy_url}/health", timeout=1)).status_code == 200
        except httpx.HTTPError:
            return False

    # --- LLM load / unload ----------------------------------------------------------------
    async def running_llms(self) -> list[str]:
        try:
            r = await self._http.get(f"{self.proxy_url}/running", timeout=3)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        items = data.get("running", data) if isinstance(data, dict) else data
        out = []
        for item in items or []:
            if isinstance(item, dict) and item.get("state", "ready") in ("ready", "starting"):
                out.append(item.get("model") or item.get("id") or item.get("name"))
            elif isinstance(item, str):
                out.append(item)
        return [m for m in out if m]

    def plan_budget(self, model_id: str, loaded_services: list[LazyService],
                    running_llms: list[str] | None = None) -> tuple[float, list[LazyService]]:
        """Return (projected GB, idle services to evict) for loading `model_id`.

        LLMs that may run alongside `model_id` (config `concurrency.sets`) stay loaded and
        count toward the budget; llama-swap unloads every other LLM.
        """
        partners = self.cfg.concurrency.partners(model_id)
        def footprint(spec) -> float:
            return spec.est_memory_gb + spec.prefix_cache_gb

        need = footprint(self.cfg.llm(model_id)) + self.reserved_gb + sum(
            footprint(self.cfg.llms[m]) for m in (running_llms or []) if m in partners and m in self.cfg.llms)
        svc_total = sum(s.spec.est_memory_gb for s in loaded_services)
        budget = self.cfg.memory_budget_gb
        evict: list[LazyService] = []
        # Evict least-recently-used services until the projection fits.
        for svc in sorted(loaded_services, key=lambda s: s._last_used):
            if need + svc_total <= budget:
                break
            evict.append(svc)
            svc_total -= svc.spec.est_memory_gb
        projected = need + svc_total
        if projected > budget:
            raise BudgetExceeded(f"{model_id} needs ~{need} GB; budget {budget} GB exceeded "
                                 f"even after evicting idle services")
        return projected, evict

    async def ensure_llm(self, model_id: str | None = None, warmup: bool = True) -> str:
        model_id = model_id or self.cfg.defaults.llm
        async with self._llm_lock:
            running = await self.running_llms()
            if model_id in running:
                return model_id
            loaded = [s for s in self.services.values() if s.loaded]
            _, evict = self.plan_budget(model_id, loaded, running)
            for svc in evict:
                await svc.unload()
            t0 = time.perf_counter()
            r = await self._http.get(f"{self.proxy_url}/upstream/{model_id}/health")
            r.raise_for_status()
            if warmup:  # first request compiles Metal kernels; pay that cost at load time
                async for _ in self.provider.chat_stream(ChatRequest(
                        model=model_id, messages=[{"role": "user", "content": "hi"}], max_tokens=1)):
                    pass
            log.info("loaded %s in %.1fs", model_id, time.perf_counter() - t0)
            return model_id

    async def unload_llm(self, model_id: str) -> None:
        await self._http.post(f"{self.proxy_url}/api/models/unload/{model_id}", timeout=60)

    def record_usage(self, model_id: str, usage: Usage) -> None:
        self.speed[model_id].add(usage)

    # --- status ----------------------------------------------------------------------------
    def _llm_footprints(self) -> dict[str, float]:
        """Map model id -> live footprint GB by matching upstream process cmdlines."""
        if not self._proxy_proc:
            procs = [p for p in psutil.process_iter(["cmdline"])
                     if p.info["cmdline"] and "llama-swap" in " ".join(p.info["cmdline"][:1])]
            root_pids = [p.pid for p in procs]
        else:
            root_pids = [self._proxy_proc.pid]
        out: dict[str, float] = {}
        for rp in root_pids:
            try:
                children = psutil.Process(rp).children()
            except psutil.Error:
                continue
            for child in children:
                try:
                    cmd = " ".join(child.cmdline())
                except psutil.Error:
                    continue
                for mid, spec in self.cfg.llms.items():
                    if spec.repo in cmd:
                        out[mid] = round(tree_footprint(child.pid) / GB, 2)
        return out

    async def status(self) -> dict:
        running = await self.running_llms()
        foot = self._llm_footprints()
        llms = []
        for mid, spec in self.cfg.llms.items():
            llms.append({
                "id": mid, "display_name": spec.display_name,
                "loaded": mid in running, "installed": llm_downloaded(spec), "memory_gb": foot.get(mid),
                "est_memory_gb": spec.est_memory_gb, "context_length": spec.context_length,
                "capabilities": spec.capabilities.model_dump(), "limits": spec.limits.model_dump(),
                "speed": self.speed[mid].summary() if mid in self.speed else None,
            })
        services = [s.status() for s in self.services.values()]
        # Own process only: llama-swap and its upstreams are children and counted per LLM.
        backend_gb = round(phys_footprint(os.getpid()) / GB, 2)
        sysmem = system_memory()
        return {
            "budget_gb": self.cfg.memory_budget_gb,
            "llms": llms,
            "services": services,
            "backend_process_gb": backend_gb,
            "models_total_gb": round(backend_gb + sum(v or 0 for v in foot.values()), 2),
            "system": sysmem.__dict__,
            "defaults": self.cfg.defaults.model_dump(),
            "video": self.video.status(),
        }

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            for svc in self.services.values():
                try:
                    await svc.maybe_unload_idle()
                except Exception:
                    log.exception("idle unload failed for %s", svc.spec.id)
