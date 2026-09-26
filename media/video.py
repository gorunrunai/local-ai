"""Video processing: scene detection, frame planning, frame extraction, proxy clips."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from media.ffmpeg import run

SCENE_RE = re.compile(rb"pts_time:([0-9.]+)")


@dataclass
class Frame:
    t: float
    path: str
    reason: str  # "scene" | "uniform"

    def to_dict(self) -> dict:
        return asdict(self)


async def detect_scenes(path: Path, threshold: float = 0.3, analysis_width: int = 320) -> list[float]:
    """Timestamps (s) where the picture changes substantially, via ffmpeg's scene score.

    Frames are downscaled before scoring, which keeps this ~20-50x faster than real time.
    """
    _, err = await run("ffmpeg", "-nostdin", "-hide_banner", "-an", "-i", str(path), "-vf",
                       f"scale={analysis_width}:-2,select='gt(scene,{threshold})',showinfo",
                       "-f", "null", "-", timeout=1800)
    return [round(float(m), 3) for m in SCENE_RE.findall(err)]


def plan_frames(duration: float, scenes: list[float], budget: int,
                scene_share: float = 0.6, settle_s: float = 0.4,
                min_interval_s: float = 2.0) -> list[tuple[float, str]]:
    """Choose up to `budget` timestamps mixing scene changes and uniform coverage.

    * Scene-change frames are sampled `settle_s` after the cut (past transition blur),
      capped at `scene_share` of the budget and thinned evenly if there are too many.
    * The remainder is filled from a uniform grid, skipping points too close to a
      chosen frame, so long static stretches are still covered.
    * The first frame is always included.
    * Short clips get fewer frames: roughly one per `min_interval_s` (at least 4), plus
      any scene changes, so a 10 s clip doesn't burn the whole budget.
    """
    if duration <= 0 or budget <= 0:
        return []
    budget = min(budget, max(4, math.ceil(duration / min_interval_s) + len(scenes)))
    end = max(0.0, duration - 0.05)
    min_gap = duration / (budget * 2.5)
    chosen: list[tuple[float, str]] = [(min(0.5, end / 2), "uniform")]

    scene_pts = sorted({round(min(t + settle_s, end), 3) for t in scenes if 0 < t < duration})
    max_scene = max(0, int(budget * scene_share) - 1)
    if len(scene_pts) > max_scene:
        scene_pts = [scene_pts[round(i * (len(scene_pts) - 1) / max(1, max_scene - 1))]
                     for i in range(max_scene)] if max_scene else []
    for t in scene_pts:
        if all(abs(t - c) >= min_gap for c, _ in chosen):
            chosen.append((t, "scene"))

    remaining = budget - len(chosen)
    if remaining > 0:
        grid = [(i + 0.5) * duration / (remaining + 1) for i in range(remaining + 1)]
        # Prefer grid points far from existing frames.
        grid.sort(key=lambda g: -min(abs(g - c) for c, _ in chosen))
        for g in grid:
            if len(chosen) >= budget:
                break
            if all(abs(g - c) >= min_gap for c, _ in chosen):
                chosen.append((round(min(g, end), 3), "uniform"))
    return sorted(chosen)[:budget]


async def extract_frames(path: Path, plan: list[tuple[float, str]], out_dir: Path,
                         max_side: int, concurrency: int = 6, progress=None) -> list[Frame]:
    """Grab one JPEG per planned timestamp (fast input seeking, parallel ffmpeg calls)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def grab(i: int, t: float, reason: str) -> Frame:
        nonlocal done
        dest = out_dir / f"frame_{i:03d}_{t:08.2f}.jpg"
        scale = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
        async with sem:
            await run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                      "-frames:v", "1", "-vf", scale, "-q:v", "3", "-map_metadata", "-1",
                      str(dest), timeout=120)
        done += 1
        if progress:
            await progress(done / len(plan))
        return Frame(t=t, path=str(dest), reason=reason)

    frames = await asyncio.gather(*(grab(i, t, r) for i, (t, r) in enumerate(plan)))
    return [f for f in frames if Path(f.path).exists() and Path(f.path).stat().st_size > 0]


async def make_proxy_clip(path: Path, dest: Path, fps: float, max_side: int,
                          max_seconds: float | None = None) -> Path:
    """Low-fps, downscaled, silent copy for native video input (fast to decode upstream)."""
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path)]
    if max_seconds:
        args += ["-t", f"{max_seconds:.2f}"]
    args += ["-an", "-vf", f"fps={fps},scale='min({max_side},iw)':-2", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-map_metadata", "-1",
             str(dest)]
    await run(*args, timeout=1800)
    return dest


def fmt_ts(seconds: float) -> str:
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
