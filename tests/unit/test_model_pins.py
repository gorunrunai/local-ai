"""Every model the installer downloads is pinned to an exact, tested Hugging Face commit."""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

from inference.config import ROOT, load_config

spec = importlib.util.spec_from_file_location("download_models", ROOT / "scripts" / "download_models.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)


def downloadable_repos() -> set[str]:
    cfg = load_config()
    repos = {s.repo for s in cfg.llms.values()}
    for group in (cfg.stt, cfg.tts, cfg.embeddings, cfg.rerankers):
        for s in group.values():
            repos.add(s.repo)
            repos |= {r for r, _ in dm.EXTRA.get(s.engine, [])}
    for v in cfg.video.values():
        repos.add(v.repo)
        repos |= {r for r, _ in v.extra_repos}
        if v.text_encoder:
            repos.add(v.text_encoder)
    repos.add(dm.SMART_TURN[0])
    return repos


def test_every_downloadable_repo_is_pinned_to_a_full_commit():
    pins = load_config().revisions
    missing = sorted(downloadable_repos() - set(pins))
    assert not missing, f"add these under `revisions:` in config/models.yaml: {missing}"
    for repo, sha in pins.items():
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"{repo}: {sha!r} is not a full commit hash"


def test_fetch_downloads_the_pinned_commit_and_points_the_cache_at_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dm, "snapshot_download", lambda repo, **kw: calls.append((repo, kw)) or str(tmp_path))
    monkeypatch.setattr(dm.constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(dm, "REVISIONS", {"org/model": "a" * 40})
    dm.fetch("org/model", ["*.json"])
    assert calls == [("org/model", {"revision": "a" * 40, "allow_patterns": ["*.json"], "ignore_patterns": dm.SKIP})]
    assert (tmp_path / "models--org--model" / "refs" / "main").read_text() == "a" * 40   # offline loads get it


def test_an_unpinned_repo_is_refused(monkeypatch):
    monkeypatch.setattr(dm, "REVISIONS", {})
    with pytest.raises(SystemExit, match="no pinned revision"):
        dm.fetch("org/unknown")


def _wan(tmp_path, monkeypatch):
    from inference import video

    cfg = load_config()
    spec = cfg.video["wan-2.2-5b"]
    monkeypatch.setattr(video, "MODEL_CACHE", tmp_path / "cache")
    monkeypatch.setattr(video, "ROOT", tmp_path / "program")
    monkeypatch.setattr(dm, "REVISIONS", dict(cfg.revisions))
    monkeypatch.setattr(dm, "ROOT", tmp_path / "program")
    return cfg, spec, video


def test_converted_wan_lives_outside_the_program_folder(tmp_path, monkeypatch):
    _, spec, video = _wan(tmp_path, monkeypatch)
    assert video.converted_dir(spec) == tmp_path / "cache" / "video" / "wan2.2-ti2v-5b-mlx"
    assert video.weights_dir(spec) is None
    legacy = video.legacy_converted_dir(spec)               # where older installs put it
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}")
    assert video.weights_dir(spec) == legacy                 # still usable before it's moved


def test_an_existing_conversion_is_moved_not_downloaded_again(tmp_path, monkeypatch):
    cfg, spec, video = _wan(tmp_path, monkeypatch)
    legacy = video.legacy_converted_dir(spec)
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}")
    fetched = []
    monkeypatch.setattr(dm, "fetch", lambda repo, patterns=None: fetched.append(repo) or str(tmp_path))
    dm.fetch_video(cfg, ["wan-2.2-5b"])
    out = video.converted_dir(spec)
    assert (out / "config.json").exists() and not legacy.exists()
    assert (out / ".source-revision").read_text() == cfg.revisions[spec.repo]
    assert spec.repo not in fetched                          # only the small tokenizer repo is checked
    # A new pinned revision means converting again.
    monkeypatch.setitem(dm.REVISIONS, spec.repo, "b" * 40)
    ran = []
    monkeypatch.setattr(dm.subprocess, "run", lambda cmd, check: ran.append(cmd) or
                        (pathlib.Path(cmd[cmd.index("--output-dir") + 1]).mkdir(parents=True) or
                         (pathlib.Path(cmd[cmd.index("--output-dir") + 1]) / "config.json").write_text("{}")))
    monkeypatch.setattr(dm, "drop_from_cache", lambda repo: None)
    (tmp_path / "program" / "videogen" / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "program" / "videogen" / ".venv" / "bin" / "python").write_text("")
    dm.fetch_video(cfg, ["wan-2.2-5b"])
    assert ran and spec.repo in fetched
    assert (out / ".source-revision").read_text() == "b" * 40 and not out.with_name(out.name + ".partial").exists()


def test_an_old_duplicate_is_removed_once_the_shared_copy_exists(tmp_path, monkeypatch):
    cfg, spec, video = _wan(tmp_path, monkeypatch)
    out, legacy = video.converted_dir(spec), video.legacy_converted_dir(spec)
    for d in (out, legacy):
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
    (out / ".source-revision").write_text(cfg.revisions[spec.repo])
    monkeypatch.setattr(dm, "fetch", lambda repo, patterns=None: str(tmp_path))
    dm.fetch_video(cfg, ["wan-2.2-5b"])
    assert (out / "config.json").exists() and not legacy.exists()
