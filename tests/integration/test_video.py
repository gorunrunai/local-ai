"""Real renders with the local video engines (opt-in: each takes a minute or more).

    RUN_VIDEO_TESTS=1 uv run pytest tests/integration/test_video.py -s

Runs the engines directly (no LLM needed) and checks the files with ffprobe.
"""

from __future__ import annotations

import os

import pytest

from inference.config import get_config
from inference.video import VideoGenerator, VideoJob, installed
from media.ffmpeg import probe

pytestmark = pytest.mark.skipif(os.environ.get("RUN_VIDEO_TESTS") != "1",
                                reason="set RUN_VIDEO_TESTS=1 to render real clips")


class _NoModels:
    """Manager stand-in: nothing else is loaded, so nothing needs unloading."""

    def __init__(self):
        self.cfg = get_config()
        self.services: dict = {}
        self.reserved_gb = 0.0

    async def running_llms(self):
        return []


@pytest.mark.parametrize("model", list(get_config().video))
async def test_render_one_second(model, tmp_path):
    spec = get_config().video[model]
    ok, why = installed(spec)
    if not ok:
        pytest.skip(why)
    stages = []

    async def progress(u):
        stages.append(u["stage"])

    gen = VideoGenerator(_NoModels())  # type: ignore[arg-type]
    res = await gen.generate(VideoJob("A red paper boat drifting on a calm pond, gentle ripples, soft morning "
                                      "light, birds chirping", model, seconds=1, seed=7), tmp_path, progress)
    info = await probe(res.path)
    print(f"\n{model}: {res.width}x{res.height} {info.duration_s:.2f}s in {res.elapsed_s}s, peak {res.peak_gb} GB")
    assert (info.width, info.height) == (res.width, res.height)
    assert abs(info.duration_s - res.frames / res.fps) < 0.25
    assert info.has_audio == spec.audio
    assert res.peak_gb <= spec.est_memory_gb + 2, "update est_memory_gb in config/models.yaml"
    assert stages, "no progress was reported"
