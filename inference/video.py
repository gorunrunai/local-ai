"""Local video generation: LTX-2.3 and Wan 2.2 run as subprocesses in the `videogen/` venv.

The engines live in their own environment (their dependency pins differ from the chat stack)
and each render is a fresh process, so every byte of video-model memory is returned to the
system when it exits. Before a render the manager makes room inside `memory_budget_gb`:
other LLMs (the Gemma listener) go first, then idle services, and only as a last resort the
chat model, which llama-swap reloads on the next request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import re
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from inference.config import MODEL_CACHE, ROOT, VideoSpec
from inference.memory import tree_footprint

if TYPE_CHECKING:
    from inference.manager import ModelManager

log = logging.getLogger(__name__)
GB = 1024 ** 3
VENV = ROOT / "videogen" / ".venv"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TQDM = re.compile(r"^(?P<desc>[^|:]{1,60}):\s*\d+%\|[^|]*\|\s*(?P<n>\d+)/(?P<total>\d+)")
PHASE = re.compile(r"^\[(?P<label>[^\]]{1,60})\] \.\.\.$")  # ltx-2-mlx: "[Loading DiT] ..."
STAGE = re.compile(r"^(?P<label>[A-Z][\w ()/,-]{2,60})\.\.\.$")  # mlx-video: "Encoding text..."

Progress = Callable[[dict], Awaitable[None]]


class VideoUnavailable(RuntimeError):
    """The engine or its weights are not installed."""


class VideoFailed(RuntimeError):
    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


@dataclass
class VideoJob:
    prompt: str
    model: str
    seconds: float = 4.0
    aspect: str = "landscape"
    image: Path | None = None
    seed: int | None = None


@dataclass
class VideoResult:
    path: Path
    model: str
    width: int
    height: int
    frames: int
    fps: int
    seed: int
    elapsed_s: float
    peak_gb: float
    audio: bool
    unloaded: list[str] = field(default_factory=list)


def frame_count(spec: VideoSpec, seconds: float) -> int:
    """Nearest valid frame count (1 + k * frame_step) for the requested duration."""
    seconds = min(max(seconds, 1.0), spec.max_seconds)
    k = max(1, round(seconds * spec.fps / spec.frame_step))
    return 1 + k * spec.frame_step


def _local_snapshot(repo: str, patterns: list[str] | None) -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        path = Path(snapshot_download(repo, allow_patterns=patterns, local_files_only=True))
    except Exception:  # noqa: BLE001 - any hub error (not cached, bad ref) means "not installed"
        return None
    exact = [p for p in patterns or [] if not any(c in p for c in "*?[")]
    return path if all((path / p).exists() for p in exact) else None


def converted_dir(spec: VideoSpec) -> Path:
    """Where the MLX conversion of this model lives (shared, outside the program folder)."""
    return MODEL_CACHE / str(spec.convert_to)


def legacy_converted_dir(spec: VideoSpec) -> Path:
    """Where earlier versions put it: inside the program folder (moved out on the next download)."""
    return ROOT / "models" / str(spec.convert_to)


def weights_dir(spec: VideoSpec) -> Path | None:
    if spec.convert_to:
        for out in (converted_dir(spec), legacy_converted_dir(spec)):
            if (out / "config.json").exists():
                return out
        return None
    return _local_snapshot(spec.repo, spec.files)


def installed(spec: VideoSpec) -> tuple[bool, str]:
    if not (VENV / "bin" / "python").exists():
        return False, "video engines not installed (run `make video-setup`)"
    if weights_dir(spec) is None:
        return False, "weights missing (run `make models-video`)"
    if spec.text_encoder and _local_snapshot(spec.text_encoder, None) is None:
        return False, f"text encoder {spec.text_encoder} missing (run `make models-video`)"
    return True, ""


def build_command(spec: VideoSpec, job: VideoJob, out: Path, seed: int) -> tuple[list[str], int, int, int]:
    width, height = spec.sizes.get(job.aspect) or next(iter(spec.sizes.values()))
    frames = frame_count(spec, job.seconds)
    wdir = weights_dir(spec)
    if spec.engine == "ltx-2-mlx":
        gemma = _local_snapshot(spec.text_encoder, None) if spec.text_encoder else None
        cmd = [str(VENV / "bin" / "ltx-2-mlx"), "generate", "--distilled", "--prompt", job.prompt,
               "--output", str(out), "--model", str(wdir), "-H", str(height), "-W", str(width),
               "--frames", str(frames), "--frame-rate", str(spec.fps), "--seed", str(seed)]
        if gemma:
            cmd += ["--gemma", str(gemma)]
    else:
        # videogen/wan_run.py wraps mlx-video's generator with memory fixes (bf16 T5, smaller VAE tiles)
        cmd = [str(VENV / "bin" / "python"), str(ROOT / "videogen" / "wan_run.py"), "--model-dir", str(wdir),
               "--prompt", job.prompt, "--width", str(width), "--height", str(height),
               "--num-frames", str(frames), "--seed", str(seed), "--output-path", str(out)]
        if spec.steps:
            cmd += ["--steps", str(spec.steps)]
    if job.image:
        cmd += ["--image", str(job.image)]
    return cmd, width, height, frames


async def normalize_audio(path: Path) -> None:
    """Bring the soundtrack to -16 LUFS (LTX's raw audio is often too quiet to hear); video is copied."""
    from media.ffmpeg import FFmpegError, run

    tmp = path.with_suffix(".norm.mp4")
    try:
        await run("ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(path), "-c:v", "copy",
                  "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(tmp), timeout=120)
        tmp.replace(path)
    except FFmpegError:
        log.warning("audio normalization failed for %s; keeping the original", path.name)
        tmp.unlink(missing_ok=True)


def parse_progress(line: str) -> dict | None:
    """Turn one line of engine output into a progress update (or None)."""
    line = ANSI.sub("", line).strip()
    if m := TQDM.match(line):
        return {"stage": m["desc"].strip(), "step": int(m["n"]), "total": int(m["total"])}
    if m := PHASE.match(line) or STAGE.match(line):
        return {"stage": m["label"].strip()}
    return None


class VideoGenerator:
    """One render at a time; progress is streamed to the caller while the engine runs."""

    def __init__(self, manager: ModelManager):
        self.manager = manager
        self.cfg = manager.cfg
        self._lock = asyncio.Lock()
        self.active: dict | None = None

    def spec(self, model_id: str | None) -> VideoSpec:
        key = model_id or self.cfg.defaults.video
        if not key or key not in self.cfg.video:
            raise VideoUnavailable(f"unknown video model {key!r}; known: {sorted(self.cfg.video)}")
        return self.cfg.video[key]

    def status(self) -> list[dict]:
        out = []
        for vid, spec in self.cfg.video.items():
            ok, why = installed(spec)
            out.append({"id": vid, "display_name": spec.display_name, "installed": ok, "reason": why,
                        "est_memory_gb": spec.est_memory_gb, "max_seconds": spec.max_seconds,
                        "sizes": spec.sizes, "audio": spec.audio, "image_input": spec.image_input,
                        "default": vid == self.cfg.defaults.video,
                        "running": bool(self.active and self.active["model"] == vid)})
        return out

    async def make_room(self, need_gb: float) -> list[str]:
        """Unload models until `need_gb` fits in the budget; returns what was unloaded."""
        m = self.manager
        budget = self.cfg.memory_budget_gb

        def llm_gb(mid: str) -> float:
            s = self.cfg.llms[mid]
            return s.est_memory_gb + s.prefix_cache_gb

        chat = self.cfg.defaults.llm
        running = [r for r in await m.running_llms() if r in self.cfg.llms]
        services = sorted((s for s in m.services.values() if s.loaded), key=lambda s: s._last_used)
        used = sum(llm_gb(r) for r in running) + sum(s.spec.est_memory_gb for s in services)
        unloaded: list[str] = []
        # Eviction order: other LLMs (Gemma listener), idle services (LRU), then the chat model.
        for mid in [r for r in running if r != chat]:
            if need_gb + used <= budget:
                break
            await m.unload_llm(mid)
            used -= llm_gb(mid)
            unloaded.append(mid)
        for s in services:
            if need_gb + used <= budget:
                break
            await s.unload()
            used -= s.spec.est_memory_gb
            unloaded.append(s.spec.id)
        if need_gb + used > budget and chat in running:
            await m.unload_llm(chat)
            unloaded.append(chat)
        return unloaded

    async def generate(self, job: VideoJob, out_dir: Path, progress: Progress | None = None,
                       cancel: asyncio.Event | None = None) -> VideoResult:
        spec = self.spec(job.model)
        ok, why = installed(spec)
        if not ok:
            raise VideoUnavailable(f"{spec.display_name}: {why}")
        if job.image and not spec.image_input:
            raise VideoUnavailable(f"{spec.display_name} can't animate an image")

        async def report(update: dict) -> None:
            if progress:
                with contextlib.suppress(Exception):
                    await progress(update)

        if self._lock.locked():
            await report({"stage": "Waiting for the current video to finish"})
        async with self._lock:
            unloaded = await self.make_room(spec.est_memory_gb)
            self.manager.reserved_gb = spec.est_memory_gb
            seed = job.seed if job.seed is not None else random.randrange(2 ** 31)
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{spec.id}-{int(time.time())}-{seed}.mp4"
            cmd, width, height, frames = build_command(spec, job, out, seed)
            self.active = {"model": spec.id, "started": time.time()}
            try:
                elapsed, peak = await self._run(cmd, report, cancel)
            finally:
                self.active = None
                self.manager.reserved_gb = 0.0
            if not out.exists():
                raise VideoFailed("the engine finished without writing a video")
            if spec.audio:
                await report({"stage": "Balancing sound level"})
                await normalize_audio(out)
            return VideoResult(out, spec.id, width, height, frames, spec.fps, seed, elapsed, peak,
                               spec.audio, unloaded)

    async def _run(self, cmd: list[str], report: Progress, cancel: asyncio.Event | None) -> tuple[float, float]:
        env = {"PATH": f"{VENV / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
               "HOME": os.environ.get("HOME", ""), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
               "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUNBUFFERED": "1", "LANG": "en_US.UTF-8",
               "TOKENIZERS_PARALLELISM": "false"}
        if os.environ.get("HF_HOME"):
            env["HF_HOME"] = os.environ["HF_HOME"]
        log.info("video: %s", " ".join(cmd[:4]))
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ROOT), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        tail: deque[str] = deque(maxlen=40)
        peak = 0.0
        last: dict = {}
        last_sent = 0.0
        passes: dict[str, int] = {}     # a progress bar that restarts = another pass (LTX refine stage)
        finished: set[str] = set()

        async def handle(raw: bytes) -> None:
            nonlocal last, last_sent
            line = raw.decode(errors="replace")
            if not line.strip():
                return
            tail.append(ANSI.sub("", line).rstrip())
            if (upd := parse_progress(line)) is None:
                return
            if (total := upd.get("total")) is not None:
                name = upd["stage"]
                if name in finished and upd["step"] < total:
                    finished.discard(name)
                    passes[name] = passes.get(name, 1) + 1
                if upd["step"] == total:
                    finished.add(name)
                if name in passes:
                    upd["stage"] = f"{name} · pass {passes[name]}"
            now = time.perf_counter()
            if upd.get("stage") != last.get("stage") or now - last_sent > 0.5 \
                    or upd.get("step") == upd.get("total"):
                last, last_sent = upd, now
                await report({**upd, "elapsed_s": round(now - t0, 1)})

        async def pump() -> None:
            assert proc.stdout
            buf = b""
            while chunk := await proc.stdout.read(4096):
                buf += chunk
                *lines, buf = re.split(rb"[\r\n]", buf)  # tqdm redraws with \r
                for raw in lines:
                    await handle(raw)
            await handle(buf)

        async def watch_memory() -> None:
            nonlocal peak
            while True:
                with contextlib.suppress(Exception):
                    peak = max(peak, tree_footprint(proc.pid) / GB)
                await asyncio.sleep(1)

        pump_task = asyncio.create_task(pump())
        mem_task = asyncio.create_task(watch_memory())
        cancel_task = asyncio.create_task(cancel.wait()) if cancel else None
        try:
            waiters = {pump_task} | ({cancel_task} if cancel_task else set())
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task and cancel_task.done():
                raise asyncio.CancelledError
            await proc.wait()
        except asyncio.CancelledError:
            self._kill(proc)
            await proc.wait()
            raise VideoFailed("stopped", "\n".join(tail)) from None
        finally:
            for t in (mem_task, cancel_task, pump_task):
                if t and not t.done():
                    t.cancel()
            if proc.returncode is None:
                self._kill(proc)
        elapsed = time.perf_counter() - t0
        if proc.returncode != 0:
            raise VideoFailed(f"engine exited with code {proc.returncode}", "\n".join(tail))
        return round(elapsed, 1), round(peak, 1)

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
