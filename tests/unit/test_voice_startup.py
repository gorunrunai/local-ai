"""Voice mode says "Listening" only once a reply can actually happen (reported: the first start
showed "Listening" for tens of seconds while nothing could answer)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.voice import session as vs


class FakeService:
    def __init__(self, loaded, log, name):
        self.loaded, self.log, self.name = loaded, log, name

    async def ensure_loaded(self):
        await asyncio.sleep(0.01)
        self.log.append(self.name)
        self.loaded = True


def make_session(monkeypatch, *, loaded: bool, mode="conversation"):
    log: list[str] = []
    stt, tts = FakeService(loaded, log, "stt"), FakeService(loaded, log, "tts")
    running = ["qwen"] if loaded else []

    async def running_llms():
        return running

    manager = SimpleNamespace(stt=lambda: stt, tts=lambda: tts, running_llms=running_llms,
                              cfg=SimpleNamespace(defaults=SimpleNamespace(llm="qwen")))

    class FakeChat:
        def __init__(self, state):
            pass

        async def warm_up(self):
            await asyncio.sleep(0.01)
            log.append("model")
            running.append("qwen")

    monkeypatch.setattr(vs, "ChatService", FakeChat)
    monkeypatch.setattr(vs, "SileroVAD", lambda: SimpleNamespace(prob=lambda f: 0.0, reset=lambda: None))
    monkeypatch.setattr(vs, "smart_turn", lambda: None)
    s = vs.VoiceSession(SimpleNamespace(manager=manager), ws=None, mode=mode, conversation_id="c",
                        voice=None, speed=1.0, sensitivity=0.5)
    sent: list[dict] = []

    async def send(obj, audio=None):
        sent.append(obj)

    s.send = send
    return s, sent, log


async def test_first_start_reports_loading_and_listens_only_when_ready(monkeypatch):
    s, sent, log = make_session(monkeypatch, loaded=False)
    task = asyncio.create_task(s._get_ready())
    await asyncio.sleep(0)
    await s.on_audio(b"\x01\x00" * 4096)                 # speech while loading is ignored
    assert s.pending == b"" and not s._ready
    await task
    states = [m["state"] for m in sent if m["type"] == "state"]
    steps = [m["step"] for m in sent if m["type"] == "loading"]
    assert states == ["loading", "listening"]
    assert steps == ["speech", "voice", "model"] and log == ["stt", "tts", "model"]
    assert s._ready


async def test_already_loaded_goes_straight_to_listening(monkeypatch):
    s, sent, log = make_session(monkeypatch, loaded=True)
    await s._get_ready()
    assert [m for m in sent if m["type"] == "loading"] == [] and log == []
    assert [m["state"] for m in sent if m["type"] == "state"] == ["listening"]


@pytest.mark.parametrize("fail", ["tts", "model"])
async def test_a_failed_step_still_ends_in_listening(monkeypatch, fail):
    s, sent, _ = make_session(monkeypatch, loaded=False)

    async def boom(*a, **k):
        raise RuntimeError("no")

    if fail == "tts":
        s.s.manager.tts().ensure_loaded = boom
    else:
        monkeypatch.setattr(vs.ChatService, "warm_up", boom)
    await s._get_ready()
    assert [m["state"] for m in sent if m["type"] == "state"][-1] == "listening"


async def test_dictation_records_from_the_start(monkeypatch):
    s, sent, log = make_session(monkeypatch, loaded=False, mode="dictation")
    await s._get_ready()
    assert s._ready and [m["state"] for m in sent if m["type"] == "state"] == ["listening"]
    assert [m for m in sent if m["type"] == "loading"] == [] and log == ["stt"]
