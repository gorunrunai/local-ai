"""Home-screen setup summary: features switch off with a reason, limitations match the setup."""

from __future__ import annotations

import pytest

from inference.config import load_config
from orchestrator import capabilities as caps


def setup(monkeypatch, *, chat="qwen3.5-35b-a3b", listener=True, videos=(), search=True, tailscale=True):
    cfg = load_config()
    cfg.defaults.llm = chat
    if chat.startswith("gemma"):
        cfg.defaults.audio_listener = chat
    monkeypatch.setattr(caps, "llm_downloaded", lambda spec: spec.id == chat or (listener and spec.id == "gemma-4-12b"))
    monkeypatch.setattr(caps, "video_installed", lambda spec: (spec.id in videos, ""))
    monkeypatch.setattr(caps.Searxng, "installed", staticmethod(lambda: search))
    monkeypatch.setattr(caps, "tailscale_binary", lambda: "/usr/bin/true" if tailscale else None)
    return cfg


def feats(r):
    return {f["id"]: f for f in r["features"]}


def texts(r):
    return [x["text"] for x in r["limitations"]]


def test_gemma_on_32gb(monkeypatch):
    r = caps.report(setup(monkeypatch, chat="gemma-4-12b"), {"chip": "Apple M1 Max", "memory_gb": 32, "macos": "26"})
    f = feats(r)
    assert f["voice_messages"]["available"]                      # Gemma hears audio itself
    assert not f["video_creation"]["available"] and "48 GB" in f["video_creation"]["reason"]
    assert not f["animate_photo"]["available"] and "48 GB" in f["animate_photo"]["reason"]
    assert not r["tested"] and "32 GB Macs haven't been tested yet" in texts(r)
    assert any("Less capable than Qwen" in t for t in texts(r)) and "No video creation" in texts(r)
    assert all(x["detail"] for x in r["limitations"])             # every limitation explains itself


def test_qwen_without_a_listener_cannot_hear(monkeypatch):
    r = caps.report(setup(monkeypatch, listener=False), {"chip": "Apple M5 Max", "memory_gb": 64, "macos": "26"})
    f = feats(r)
    assert not f["voice_messages"]["available"] and "can't hear audio" in f["voice_messages"]["reason"]
    assert "Can't hear voice messages" in texts(r)
    assert r["tested"]


def test_video_engines(monkeypatch):
    mac = {"chip": "Apple M4 Max", "memory_gb": 48, "macos": "26"}
    r = caps.report(setup(monkeypatch, chat="gemma-4-12b", videos=("ltx-2.3",)), mac)
    assert feats(r)["video_creation"]["available"] and feats(r)["video_sound"]["available"]
    r = caps.report(setup(monkeypatch, videos=("wan-2.2-5b",)), {**mac, "memory_gb": 64})
    f = feats(r)
    assert f["video_creation"]["available"] and not f["video_sound"]["available"]
    assert "Only LTX-2.3 creates sound" in f["video_sound"]["reason"]
    assert f["animate_photo"]["available"]                        # Wan animates photos too, silently
    assert "Wan 2.2 videos have no sound" in texts(r)
    wan_time = next(x for x in r["limitations"] if x["text"].startswith("Wan 2.2 takes"))
    assert "paused" in wan_time["detail"]
    # 96 GB+: the chat model stays loaded during Wan renders
    r = caps.report(setup(monkeypatch, videos=("wan-2.2-5b",)), {**mac, "memory_gb": 96})
    assert "paused" not in next(x for x in r["limitations"] if x["text"].startswith("Wan 2.2 takes"))["detail"]


@pytest.mark.parametrize("search,tailscale", [(False, True), (True, False)])
def test_optional_services(monkeypatch, search, tailscale):
    r = caps.report(setup(monkeypatch, search=search, tailscale=tailscale),
                    {"chip": "Apple M5 Max", "memory_gb": 64, "macos": "26"})
    f = feats(r)
    assert f["web_search"]["available"] is search and f["phone"]["available"] is tailscale
    for key in ("web_search", "phone"):
        assert f[key]["available"] or f[key]["reason"]


def test_tools_hidden_when_not_installed(monkeypatch):
    from orchestrator.tools import video_gen, web

    monkeypatch.setattr(video_gen, "installed", lambda spec: (False, "missing"))
    assert not video_gen.GenerateVideo().available()
    monkeypatch.setattr(web.searxng, "installed", lambda: False)
    assert not web.WebSearch("http://127.0.0.1:8888").available()
    assert web.WebSearch("https://search.example.org").available()       # your own server: offered
