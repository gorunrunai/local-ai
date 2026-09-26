"""Download every model weight listed in config/models.yaml (used by `make setup`).

    uv run python scripts/download_models.py            # defaults: main LLM + all services
    uv run python scripts/download_models.py --all      # also heavy / fallback LLMs
    uv run python scripts/download_models.py --only qwen3.5-35b-a3b
    uv run python scripts/download_models.py --video    # video generation (LTX-2.3, Wan 2.2)

This is the only code path that talks to the network for model weights; at runtime the
backend runs with HF_HUB_OFFLINE=1.

Every repo is fetched at the exact commit pinned under `revisions:` in config/models.yaml, and the
local cache's `main` for that repo is pointed at it: the app loads models offline by name, which
resolves `main`, so it always gets the pinned weights. (The Hugging Face cache is shared: another
app loading the same repo offline would see the pinned version too, until it downloads a newer one.)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ["ALLOW_MODEL_DOWNLOADS"] = "1"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from huggingface_hub import constants, hf_hub_download, snapshot_download

from inference.config import ROOT, load_config

# Extra repos some engines fetch lazily at runtime; pre-fetch so runtime stays offline.
EXTRA = {
    "mlx-audio": [("prince-canuma/Kokoro-82M", ["voices/*.safetensors", "*.json"])],
}


# Alternative weight formats the app never loads (it uses safetensors / MLX weights). The
# reranker and embedding repos ship ONNX and OpenVINO copies too: skipping them saves ~12 GB.
SKIP = ["onnx/*", "openvino/*", "openvino_model*", "*.onnx", "*.msgpack", "*.h5", "tf_model*",
        "flax_model*", "rust_model*"]


REVISIONS: dict[str, str] = {}      # filled from config/models.yaml in main()
SMART_TURN = ("pipecat-ai/smart-turn-v3", "smart-turn-v3.2-cpu.onnx")


def pinned(repo: str) -> str:
    """The tested commit for this repo. Unpinned repos are refused, so nothing drifts silently."""
    if repo not in REVISIONS:
        raise SystemExit(f"{repo} has no pinned revision: add it under `revisions:` in config/models.yaml")
    return REVISIONS[repo]


def point_cache_at(repo: str, sha: str) -> None:
    """Make the cache's `main` for this repo the pinned commit (what offline loads resolve)."""
    ref = Path(constants.HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(sha)


def fetch(repo: str, patterns: list[str] | None = None) -> str:
    sha = pinned(repo)
    print(f"→ {repo}@{sha[:7]} {patterns or ''}", flush=True)
    path = snapshot_download(repo, revision=sha, allow_patterns=patterns, ignore_patterns=SKIP)
    point_cache_at(repo, sha)
    return path


def fetch_file(repo: str, filename: str) -> str:
    sha = pinned(repo)
    print(f"→ {repo}@{sha[:7]}/{filename}", flush=True)
    path = hf_hub_download(repo, filename, revision=sha)
    point_cache_at(repo, sha)
    return path


def drop_from_cache(repo: str) -> None:
    from huggingface_hub import scan_cache_dir

    info = scan_cache_dir()
    revs = [r.commit_hash for c in info.repos if c.repo_id == repo for r in c.revisions]
    if revs:
        strategy = info.delete_revisions(*revs)
        print(f"→ freed {strategy.expected_freed_size_str} ({repo} originals)", flush=True)
        strategy.execute()


def fetch_video(cfg, only: list[str] | None) -> None:
    """Video weights (~30 GB each). Wan ships PyTorch checkpoints: convert them once to MLX, into the
    shared model cache (~/.cache/gorunrun/models), which survives uninstalling and reinstalling."""
    from inference.video import converted_dir, legacy_converted_dir

    venv_python = ROOT / "videogen" / ".venv" / "bin" / "python"
    for vid, spec in cfg.video.items():
        if only and vid not in only:
            continue
        for repo, patterns in spec.extra_repos:
            fetch(repo, patterns)
        if not spec.convert_to:
            fetch(spec.repo, spec.files)
            continue
        out, stamp, sha = converted_dir(spec), converted_dir(spec) / ".source-revision", pinned(spec.repo)
        legacy = legacy_converted_dir(spec)
        if not (out / "config.json").exists() and (legacy / "config.json").exists():
            # Converted by an earlier version inside the program folder: move it, don't re-download.
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(out))
            stamp.write_text(sha)       # only ever converted from the pinned revision
            print(f"→ moved the existing MLX copy of {spec.repo} to {out}", flush=True)
        if (out / "config.json").exists() and stamp.exists() and stamp.read_text().strip() == sha:
            if legacy.exists():         # an old in-program copy duplicating the shared one
                shutil.rmtree(legacy, ignore_errors=True)
                print(f"→ removed the duplicate old copy in {legacy}", flush=True)
            print(f"✓ {spec.repo} already converted to MLX in {out}", flush=True)
            continue
        path = fetch(spec.repo, spec.files)
        if not venv_python.exists():
            raise SystemExit("run `make video-setup` first (the converter lives in the videogen venv)")
        print(f"→ converting {spec.repo} to MLX in {out} (one time)", flush=True)
        tmp = out.with_name(out.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run([str(venv_python), "-m", "mlx_video.models.wan_2.convert", "--checkpoint-dir", path,
                        "--output-dir", str(tmp)], check=True)
        shutil.rmtree(out, ignore_errors=True)      # an older revision's conversion, if any
        tmp.rename(out)
        stamp.write_text(sha)
        drop_from_cache(spec.repo)  # the PyTorch originals aren't used after conversion
    print("video models ready")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="download every configured LLM")
    ap.add_argument("--only", nargs="*", help="model ids to download")
    ap.add_argument("--video", nargs="*", metavar="MODEL",
                    help="download (and convert) the video-generation models; optionally only these ids")
    args = ap.parse_args()
    cfg = load_config()
    REVISIONS.update(cfg.revisions)

    if args.video is not None:
        fetch_video(cfg, args.video)
        return
    llm_ids = args.only or ([*cfg.llms] if args.all else
                            [m for m in (cfg.defaults.llm, cfg.defaults.audio_listener) if m])
    for mid in dict.fromkeys(llm_ids):  # the chat model may also be the voice listener
        if mid not in cfg.llms:
            continue
        spec = cfg.llms[mid]
        fetch(spec.repo)
    if args.only:
        return
    for group in (cfg.stt, cfg.tts, cfg.embeddings, cfg.rerankers):
        for spec in group.values():
            fetch(spec.repo)
            for repo, patterns in EXTRA.get(spec.engine, []):
                fetch(repo, patterns)
    fetch_file(*SMART_TURN)                 # turn detection
    print("all models downloaded")


if __name__ == "__main__":
    main()
