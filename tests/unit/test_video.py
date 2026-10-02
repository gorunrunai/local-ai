"""Video generation: frame math, progress parsing, commands, memory planning, tool errors."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference import video
from inference.config import get_config
from inference.video import VideoGenerator, VideoJob, build_command, frame_count, parse_progress


@pytest.fixture
def cfg():
    return get_config()


def test_frame_counts_follow_each_engine_grid(cfg):
    ltx, wan = cfg.video["ltx-2.3"], cfg.video["wan-2.2-5b"]
    assert frame_count(ltx, 4) == 97 and (97 - 1) % 8 == 0     # 1 + 8k
    assert frame_count(wan, 4) == 97 and (97 - 1) % 4 == 0     # 1 + 4k
    assert frame_count(wan, 2) == 49 and frame_count(wan, 2.1) == 53
    assert frame_count(ltx, 60) == frame_count(ltx, ltx.max_seconds)  # clamped
    assert frame_count(ltx, 0) == 25                                    # at least ~1 s


def test_sizes_fit_engine_constraints(cfg):
    for w, h in cfg.video["ltx-2.3"].sizes.values():
        assert w % 64 == 0 and h % 64 == 0     # two-stage: half-res stage 1 on a 32-px latent grid
    for w, h in cfg.video["wan-2.2-5b"].sizes.values():
        assert w % 32 == 0 and h % 32 == 0     # VAE stride 16 x patch 2


@pytest.mark.parametrize("line,expected", [
    ("Denoising (ancestral):  38%|███▊      | 3/8 [00:10<00:17,  3.4s/it]",
     {"stage": "Denoising (ancestral)", "step": 3, "total": 8}),
    ("\x1b[32mDiffusion: 100%|██████████| 40/40 [05:00<00:00]\x1b[0m", {"stage": "Diffusion", "step": 40, "total": 40}),
    ("[Loading DiT] ...", {"stage": "Loading DiT"}),
    ("\x1b[94mLoading transformer model(s)...\x1b[0m", {"stage": "Loading transformer model(s)"}),
    ("[Loading DiT] done in 4.2s", None),
    ("  Size: 1280x704, Frames: 97", None),
])
def test_progress_lines(line, expected):
    assert parse_progress(line) == expected


def test_commands(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(video, "weights_dir", lambda spec: tmp_path / spec.id)
    monkeypatch.setattr(video, "_local_snapshot", lambda repo, patterns: tmp_path / "gemma")
    job = VideoJob("a fox in snow", "ltx-2.3", seconds=4, aspect="portrait", image=tmp_path / "fox.png")
    cmd, w, h, frames = build_command(cfg.video["ltx-2.3"], job, tmp_path / "o.mp4", 7)
    assert (w, h, frames) == (512, 768, 97)
    assert cmd[1:3] == ["generate", "--distilled"] and cmd[cmd.index("--gemma") + 1] == str(tmp_path / "gemma")
    assert cmd[cmd.index("--image") + 1] == str(tmp_path / "fox.png")
    cmd, w, h, frames = build_command(cfg.video["wan-2.2-5b"], VideoJob("x", "wan-2.2-5b", 3), tmp_path / "o.mp4", 1)
    assert cmd[1].endswith("videogen/wan_run.py") and (w, h) == (960, 544) and frames == 73
    assert cmd[cmd.index("--steps") + 1] == "25"
    assert "--image" not in cmd


class _Store:
    """Messages and attachments for the "photo in the current message" fallback."""

    def __init__(self, atts, user_atts=()):
        self.atts = atts
        self.msgs = {"m2": {"id": "m2", "parent_id": "m1"}, "m1": {"id": "m1", "attachment_ids": list(user_atts)}}

    async def get_message(self, mid):
        return self.msgs.get(mid)

    async def get_attachment(self, aid):
        return self.atts.get(aid)

    async def conversation_attachments(self, cid):
        return list(self.atts.values())


class _Svc:
    def __init__(self, sid, gb, last):
        self.spec = SimpleNamespace(id=sid, est_memory_gb=gb)
        self._last_used = last
        self.loaded = True

    async def unload(self):
        self.loaded = False


class _Manager:
    def __init__(self, cfg, running, services):
        self.cfg, self.running, self.unloaded = cfg, list(running), []
        self.services = {s.spec.id: s for s in services}
        self.reserved_gb = 0.0

    async def running_llms(self):
        return list(self.running)

    async def unload_llm(self, mid):
        self.unloaded.append(mid)
        self.running.remove(mid)


async def test_make_room_evicts_listener_then_services_then_chat(cfg):
    services = [_Svc("reranker", 2.5, 1), _Svc("embeddings", 1.0, 2)]
    m = _Manager(cfg, ["qwen3.5-35b-a3b", "gemma-4-12b"], services)
    gen = VideoGenerator(m)  # type: ignore[arg-type]
    # Qwen 25 + Gemma 11 + services 3.5 = 39.5; LTX needs 18 of a 45 GB budget.
    out = await gen.make_room(18)
    assert out == ["gemma-4-12b", "reranker"]           # chat model stays loaded
    assert m.running == ["qwen3.5-35b-a3b"]

    m2 = _Manager(cfg, ["qwen3.5-35b-a3b"], [_Svc("reranker", 2.5, 1)])
    assert await VideoGenerator(m2).make_room(22) == ["reranker", "qwen3.5-35b-a3b"]  # 25 + 22 > 45

    m3 = _Manager(cfg, ["qwen3.5-35b-a3b"], [])
    assert await VideoGenerator(m3).make_room(10) == []


async def test_generate_runs_engine_and_reports_progress(cfg, monkeypatch, tmp_path):
    """A stand-in engine prints tqdm-style progress and writes the output file."""
    script = tmp_path / "engine.py"
    script.write_text(
        "import sys, time\n"
        "out = sys.argv[sys.argv.index('--output-path') + 1]\n"
        "print('Encoding text...', flush=True)\n"
        "for p in range(2):\n"
        "    for i in range(0, 4):\n"
        "        sys.stderr.write(f'\\rDiffusion: {i*33}%|###| {i}/3 [00:01<00:00]'); sys.stderr.flush(); time.sleep(0.6)\n"
        "open(out, 'wb').write(b'fake mp4')\n")
    monkeypatch.setattr(video, "installed", lambda spec: (True, ""))
    monkeypatch.setattr(video, "build_command", lambda spec, job, out, seed: (
        [sys.executable, str(script), "--output-path", str(out)], 1280, 704, 97))
    m = _Manager(cfg, [], [])
    gen = VideoGenerator(m)  # type: ignore[arg-type]
    seen = []

    async def progress(u):
        seen.append(u)
        assert m.reserved_gb == cfg.video["wan-2.2-5b"].est_memory_gb  # budget held while rendering

    res = await gen.generate(VideoJob("x", "wan-2.2-5b", seed=5), tmp_path / "out", progress)
    assert res.path.read_bytes() == b"fake mp4" and res.seed == 5 and m.reserved_gb == 0
    assert {"stage": "Encoding text"}.items() <= seen[0].items()
    assert seen[-1]["step"] == 3 and seen[-1]["total"] == 3
    assert seen[-1]["stage"] == "Diffusion · pass 2"          # second run of the same bar
    assert any(u["stage"] == "Diffusion" and u.get("step") == 3 for u in seen)


async def test_generate_can_be_stopped(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(video, "installed", lambda spec: (True, ""))
    monkeypatch.setattr(video, "build_command", lambda spec, job, out, seed: (
        [sys.executable, "-c", "import time; time.sleep(60)"], 768, 512, 97))
    gen = VideoGenerator(_Manager(cfg, [], []))  # type: ignore[arg-type]
    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.5, cancel.set)
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(video.VideoFailed, match="stopped"):
        await gen.generate(VideoJob("x", "ltx-2.3"), tmp_path, None, cancel)
    assert asyncio.get_running_loop().time() - t0 < 5
    assert gen.active is None and gen.manager.reserved_gb == 0


async def test_tool_reports_missing_install(cfg, monkeypatch, tmp_path):
    from orchestrator.tools.video_gen import GenerateVideo

    monkeypatch.setattr(video, "installed", lambda spec: (False, "weights missing (run `make models-video`)"))
    ctx = SimpleNamespace(manager=SimpleNamespace(video=VideoGenerator(_Manager(cfg, [], []))),  # type: ignore[arg-type]
                          workdir_root=tmp_path / "sandbox", cancel=None, call_id="c1", emit=None,
                          message_id="m2", store=_Store({}))
    res = await GenerateVideo(cfg).run({"prompt": "a fox"}, ctx)  # type: ignore[arg-type]
    assert not res.ok and "make models-video" in res.content
    res = await GenerateVideo(cfg).run({"prompt": "a fox", "model": "nope"}, ctx)  # type: ignore[arg-type]
    assert not res.ok and "unknown video model" in res.content
    assert Path(tmp_path).exists()


def test_photo_sets_clip_shape():
    from orchestrator.tools.video_gen import aspect_for

    assert aspect_for(1796, 2515) == "portrait"
    assert aspect_for(4032, 3024) == "landscape"
    assert aspect_for(1000, 1080) == "square"


async def test_photo_is_rotated_and_passed_to_engine(cfg, monkeypatch, tmp_path):
    """A phone photo with EXIF rotation reaches the engine upright and without metadata."""
    from PIL import Image

    from orchestrator.tools import video_gen

    src = tmp_path / "IMG_1.jpeg"
    im = Image.new("RGB", (400, 300), "red")        # stored landscape...
    exif = Image.Exif()
    exif[0x0112] = 6                                  # ...displayed rotated 90°: portrait
    im.save(src, "JPEG", exif=exif.tobytes())

    async def find(ctx, ref):
        return {"id": "att_1", "filename": "IMG_1.jpeg", "path": str(src)}

    seen = {}

    class Gen:
        def spec(self, model):
            return cfg.video["ltx-2.3"]

        async def generate(self, job, out_dir, progress, cancel):
            seen["job"] = job
            if job.image:
                with Image.open(job.image) as im:
                    seen["size"], seen["exif"] = im.size, dict(im.getexif())
            raise video.VideoFailed("stopped")

    monkeypatch.setattr(video_gen, "_find_attachment", find)
    photo = {"id": "att_1", "filename": "IMG_1.jpeg", "path": str(src)}
    ctx = SimpleNamespace(manager=SimpleNamespace(video=Gen()), workdir_root=tmp_path / "sandbox",
                          cancel=None, call_id="c1", emit=None, message_id="m2", conversation_id="conv_1",
                          store=_Store({"att_1": photo}))
    res = await video_gen.GenerateVideo(cfg).run(
        {"prompt": "he waves", "image": "IMG_1.jpeg", "aspect": "landscape"}, ctx)  # type: ignore[arg-type]
    assert not res.ok and "stopped" in res.content
    assert seen["job"].aspect == "portrait" and seen["size"] == (300, 400) and not seen["exif"]

    # The model forgot `image`, but the user's message has the photo attached: it's used anyway.
    seen.clear()
    ctx.store = _Store({"att_1": photo}, user_atts=["att_1"])
    await video_gen.GenerateVideo(cfg).run({"prompt": "he waves"}, ctx)  # type: ignore[arg-type]
    assert seen["job"].image is not None and seen["job"].aspect == "portrait"
    # No photo in the message: plain text-to-video.
    seen.clear()
    ctx.store = _Store({"att_1": photo})
    await video_gen.GenerateVideo(cfg).run({"prompt": "a fox"}, ctx)  # type: ignore[arg-type]
    assert seen["job"].image is None and seen["job"].aspect == "landscape"

    # Reported: the model guessed "image.jpg" for the photo on the message. Use that photo.
    async def missing(ctx, ref):
        return None

    monkeypatch.setattr(video_gen, "_find_attachment", missing)
    seen.clear()
    ctx.store = _Store({"att_1": photo}, user_atts=["att_1"])
    await video_gen.GenerateVideo(cfg).run({"prompt": "he smiles", "image": "image.jpg"}, ctx)  # type: ignore[arg-type]
    assert seen["job"].image is not None
    # No photo on the message: the error names the photos that are in the chat.
    ctx.store = _Store({"att_1": photo})
    res = await video_gen.GenerateVideo(cfg).run({"prompt": "he smiles", "image": "image.jpg"}, ctx)  # type: ignore[arg-type]
    assert not res.ok and "'IMG_1.jpeg'" in res.content


def test_prompts_ask_for_a_picture_without_text():
    from orchestrator.tools.video_gen import no_text

    # Reported: LTX burned "You're away night, son!" into the clip as a subtitle.
    assert no_text('He smiles and says "You\'re always right, son!"').endswith("clean, like raw camera footage.")
    assert "subtitle" not in no_text("He waves.")                  # naming it makes the model draw it
    assert no_text("A neon sign that says OPEN flickers") == "A neon sign that says OPEN flickers"


def test_tool_offers_only_installed_engines(cfg, monkeypatch):
    from orchestrator.tools import video_gen

    # Wan-only install: LTX isn't offered, and Wan becomes the default even though
    # models.yaml names LTX.
    monkeypatch.setattr(video_gen, "installed", lambda spec: (spec.id == "wan-2.2-5b", ""))
    model = video_gen.GenerateVideo(cfg).parameters["properties"]["model"]
    assert model["enum"] == ["wan-2.2-5b"] and model["default"] == "wan-2.2-5b"
    # Both installed: LTX stays the default.
    monkeypatch.setattr(video_gen, "installed", lambda spec: (True, ""))
    model = video_gen.GenerateVideo(cfg).parameters["properties"]["model"]
    assert model["enum"] == ["ltx-2.3", "wan-2.2-5b"] and model["default"] == "ltx-2.3"


# --- Settings → Video generation: the longest clip ------------------------------------------------
@pytest.fixture
def own_cfg(cfg):
    """A private copy: these tests change max_seconds, which get_config() shares with other tests."""
    return cfg.model_copy(deep=True)


def test_estimates_match_measured_renders(cfg):
    ltx = cfg.video["ltx-2.3"]
    # Measured on a 64 GB Mac at 640x640: 18.8 GB / ~59 s (5 s), 22.1 / 104 (8 s), 24.4 / 130 (10 s).
    for secs, gb, render in [(5, 18.8, 59), (8, 22.1, 104), (10, 24.4, 130)]:
        assert abs(ltx.memory_gb(secs) - gb) <= 0.3 and abs(ltx.render_s(secs) - render) <= 5
    assert ltx.memory_gb(2) == ltx.est_memory_gb          # shorter clips: no less than measured
    assert cfg.video["wan-2.2-5b"].render_s(4) == 420


def test_length_setting_applies_within_limits(own_cfg):
    ltx, wan = own_cfg.video["ltx-2.3"], own_cfg.video["wan-2.2-5b"]
    video.apply_length_settings(own_cfg, {"ltx-2.3": 15, "wan-2.2-5b": 3})
    assert ltx.max_seconds == 15 and wan.max_seconds == 3
    assert frame_count(ltx, 15) == 361                    # longer clips are rendered, not clamped to 10
    video.apply_length_settings(own_cfg, {"ltx-2.3": 99, "wan-2.2-5b": 9})
    assert ltx.max_seconds == 20 and wan.max_seconds == 5  # the model's ceiling; Wan can't go past 5
    video.apply_length_settings(own_cfg, {})               # removed from Settings: back to models.yaml
    assert ltx.max_seconds == ltx.default_max_seconds == 10 and wan.max_seconds == 5
    video.apply_length_settings(own_cfg, {"ltx-2.3": True})  # junk is ignored
    assert ltx.max_seconds == 10


def test_memory_budget_limits_the_setting(own_cfg):
    ltx = own_cfg.video["ltx-2.3"]
    assert video.length_limit(ltx, 45) == 20               # 20 s needs ~36 GB
    assert video.length_limit(ltx, 30) == 14               # 14 s ~29.2 GB fits, 15 s ~30.3 GB doesn't
    assert video.length_limit(ltx, 20) == 10               # never below the configured default
    own_cfg.memory_budget_gb = 30
    video.apply_length_settings(own_cfg, {"ltx-2.3": 20})
    assert ltx.max_seconds == 14


async def test_longer_clips_reserve_more_memory(own_cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(video, "installed", lambda spec: (True, ""))
    video.apply_length_settings(own_cfg, {"ltx-2.3": 20})
    m = _Manager(own_cfg, [], [])
    gen = VideoGenerator(m)  # type: ignore[arg-type]
    asked = []

    async def make_room(need):
        asked.append(need)
        raise RuntimeError("stop here")

    monkeypatch.setattr(gen, "make_room", make_room)
    with pytest.raises(RuntimeError):
        await gen.generate(VideoJob("x", "ltx-2.3", seconds=20), tmp_path)
    ltx = own_cfg.video["ltx-2.3"]
    assert asked == [ltx.planned_gb(20)] and asked[0] == ltx.est_memory_gb + 10 * ltx.memory_gb_per_s
    own_cfg.memory_budget_gb = 30                          # a smaller Mac: refuse rather than swap
    with pytest.raises(video.VideoUnavailable, match="more than this Mac's memory budget"):
        await gen.generate(VideoJob("x", "ltx-2.3", seconds=20), tmp_path)


def test_default_length_keeps_the_chat_model_loaded(own_cfg):
    """Up to the configured length, planning is as before: a 10 s LTX clip fits next to Qwen in a
    64 GB Mac's 44 GB budget. Longer clips, chosen in Settings, make room by unloading it."""
    own_cfg.memory_budget_gb = 44
    gen = VideoGenerator(_Manager(own_cfg, [], []))  # type: ignore[arg-type]
    ltx = own_cfg.video["ltx-2.3"]
    assert ltx.planned_gb(4) == ltx.planned_gb(10) == ltx.est_memory_gb
    assert gen.unloads_chat_from(ltx) == 11
    assert gen.unloads_chat_from(own_cfg.video["wan-2.2-5b"]) == 1      # 32 GB never fits next to it
    row = next(r for r in gen.status() if r["id"] == "ltx-2.3")
    assert (row["default_max_seconds"], row["settable_max_seconds"], row["unloads_chat_from_seconds"]) == (10, 20, 11)


def test_tool_tells_the_model_the_current_limit(own_cfg, monkeypatch):
    from orchestrator.tools import video_gen

    monkeypatch.setattr(video_gen, "installed", lambda spec: (True, ""))
    tool = video_gen.GenerateVideo(own_cfg)
    assert tool.parameters["properties"]["seconds"]["maximum"] == 10
    video.apply_length_settings(own_cfg, {"ltx-2.3": 18})   # changed in Settings, no restart
    params = tool.parameters["properties"]
    assert params["seconds"]["maximum"] == 18 and "ltx-2.3: LTX-2.3 22B distilled (4-bit) · video + sound, up to 18s" in params["model"]["description"]
    assert "Settings → Models → Video generation" in tool.description
    video.apply_length_settings(own_cfg, {"ltx-2.3": 2, "wan-2.2-5b": 2})
    assert tool.parameters["properties"]["seconds"] == {"type": "number", "minimum": 1, "maximum": 2, "default": 2}


async def test_settings_api_checks_video_lengths(own_cfg):
    from fastapi import HTTPException

    from orchestrator import api

    class Persistent:
        def __init__(self):
            self.saved = {}

        async def get_setting(self, key, default=None):
            return self.saved.get(key, default)

        async def set_setting(self, key, value):
            self.saved[key] = value

    applied = []

    class State:
        manager = SimpleNamespace(cfg=own_cfg)
        persistent = Persistent()

        async def prefs(self):
            return {**api.DEFAULT_PREFS, **self.persistent.saved.get("prefs", {})}

        def apply_prefs(self, prefs):
            applied.append(prefs["video_max_seconds"])

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=State())))
    out = await api.patch_settings({"video_max_seconds": {"ltx-2.3": 15}}, request)  # type: ignore[arg-type]
    assert out["video_max_seconds"] == {"ltx-2.3": 15} and applied == [{"ltx-2.3": 15}]
    for bad, why in [({"ltx-2.3": 21}, "choose 1 to 20 seconds"), ({"ltx-2.3": 0}, "choose 1 to 20"),
                     ({"wan-2.2-5b": 6}, "choose 1 to 5"), ({"sora": 5}, "unknown video model"),
                     ({"ltx-2.3": "15"}, "choose 1 to 20"), ([15], "must map")]:
        with pytest.raises(HTTPException, match=why):
            await api.patch_settings({"video_max_seconds": bad}, request)  # type: ignore[arg-type]
