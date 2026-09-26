"""Acceptance test 6 (voice mode round trip) plus barge-in and streaming dictation.

A scripted client streams WAV audio to /api/voice in real time (32 ms frames, silence in
between) and records every server event with its arrival time.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time

import httpx
import numpy as np
import pytest
import soundfile as sf
import websockets

from tests.integration.conftest import BASE, FIX
from tests.wer import wer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
FRAME = 512
WS = BASE.replace("http", "ws") + "/voice"


def pcm(name: str) -> np.ndarray:
    audio, sr = sf.read(FIX / name, dtype="float32")
    assert sr == 16000
    return (np.clip(audio, -1, 1) * 32767).astype("<i2")


class Client:
    """Mic simulator: plays queued clips in real time, otherwise streams silence."""

    def __init__(self, ws):
        self.ws = ws
        self.events: list[tuple[float, dict]] = []
        self.audio: list[tuple[float, int]] = []
        self.clips: asyncio.Queue = asyncio.Queue()
        self.speech_end: list[float] = []
        self._stop = asyncio.Event()

    async def mic(self):
        silence = np.zeros(FRAME, dtype="<i2")
        t = time.monotonic()
        while not self._stop.is_set():
            clip = None if self.clips.empty() else self.clips.get_nowait()
            frames = [clip[i:i + FRAME] for i in range(0, len(clip), FRAME)] if clip is not None else [silence]
            for f in frames:
                if len(f) < FRAME:
                    f = np.pad(f, (0, FRAME - len(f)))
                await self.ws.send(f.tobytes())
                t += 0.032
                await asyncio.sleep(max(0, t - time.monotonic()))
            if clip is not None:
                self.speech_end.append(time.monotonic())

    async def listen(self):
        async for msg in self.ws:
            now = time.monotonic()
            if isinstance(msg, bytes):
                self.audio.append((now, len(msg)))
            else:
                self.events.append((now, json.loads(msg)))

    async def wait_for(self, pred, timeout=60, after=0.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for t, ev in self.events:
                if t >= after and pred(ev):
                    return t, ev
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out; events: {[e for _, e in self.events][-8:]}")

    def stop(self):
        self._stop.set()


async def session(mode="conversation"):
    ws = await websockets.connect(f"{WS}?mode={mode}", max_size=None)
    c = Client(ws)
    ready = json.loads(await ws.recv())
    c.conversation_id = ready["conversation_id"]
    c.tasks = [asyncio.create_task(c.mic()), asyncio.create_task(c.listen())]
    return c


async def close(c: Client):
    c.stop()
    await c.ws.close()
    for t in c.tasks:
        t.cancel()


async def test_6_voice_round_trip_latency(server):
    lat, rows = [], []
    for _ in range(3):
        c = await session()
        try:
            await c.clips.put(pcm("voice_question.wav"))
            _, final = await c.wait_for(lambda e: e["type"] == "final_transcript")
            first_audio = None
            deadline = time.monotonic() + 60
            while first_audio is None and time.monotonic() < deadline:
                first_audio = c.audio[0][0] if c.audio else None
                await asyncio.sleep(0.01)
            _, m = await c.wait_for(lambda e: e["type"] == "metrics" and e.get("final"))
            reply = "".join(e["text"] for _, e in c.events if e["type"] == "assistant_delta")
            ms = round((first_audio - c.speech_end[0]) * 1000)
            lat.append(ms)
            rows.append((ms, m, final["text"], reply))
            assert "capital of australia" in final["text"].lower()
            assert "canberra" in reply.lower()
        finally:
            await close(c)
    print("\nVoice round trip (end of speech → first reply audio):")
    for ms, m, heard, reply in rows:
        print(f"  {ms:5d} ms | wait {m['turn_wait_ms']} · STT {m['stt_ms']} · LLM {m['llm_first_text_ms']} "
              f"· TTS {m['tts_first_ms']} | heard {heard!r} → {reply!r}")
    print(f"  median {statistics.median(lat)} ms (target < 2000 ms)")
    assert statistics.median(lat) < 2000


async def test_6b_barge_in_interrupts_and_answers_new_question(server):
    c = await session()
    try:
        await c.clips.put(pcm("voice_story.wav"))
        await c.wait_for(lambda e: e["type"] == "state" and e["state"] == "speaking", timeout=90)
        await asyncio.sleep(0.8)  # let the story play a little
        t_barge = time.monotonic()
        await c.clips.put(pcm("voice_barge.wav"))
        t_int, ev = await c.wait_for(lambda e: e["type"] == "interrupted", after=t_barge, timeout=10)
        reaction = round((t_int - t_barge) * 1000)
        _, final = await c.wait_for(lambda e: e["type"] == "final_transcript" and e["turn"] == 2, timeout=30)
        # The interrupted reply also reports turn_done (status "interrupted"); wait for the new one.
        await c.wait_for(lambda e: e["type"] == "turn_done" and e["status"] != "interrupted", after=t_int, timeout=60)
        reply2 = "".join(e["text"] for t, e in c.events if e["type"] == "assistant_delta" and t > t_int)
        print(f"\nBarge-in: interrupted {reaction} ms after the user started talking ({ev['reason']}); "
              f"heard {final['text']!r} → {reply2[:80]!r}")
        assert ev["reason"] == "barge_in" and reaction < 1500
        assert re.search(r"(two|2) plus (two|2)", final["text"].lower())
        assert "4" in reply2 or "four" in reply2.lower()
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient() as http:
            detail = (await http.get(f"{BASE}/conversations/{c.conversation_id}")).json()
        replies = [m for m in detail["messages"] if m["role"] == "assistant"]
        assert replies[0]["status"] == "stopped" and "interrupted" in replies[0]["content"]
    finally:
        await close(c)


async def test_streaming_dictation(server, expected):
    c = await session("dictation")
    try:
        await c.clips.put(pcm("dictation.wav"))
        _, final = await c.wait_for(lambda e: e["type"] == "final_transcript")
        partials = [e for _, e in c.events if e["type"] == "partial_transcript"]
        score = wer(expected["dictation_text"], final["text"])
        print(f"\nDictation: {len(partials)} live partials, final WER {score:.3f}: {final['text']!r}")
        assert partials, "expected live partial transcripts while speaking"
        assert score <= 0.1
        assert not any(e["type"] == "assistant_delta" for _, e in c.events)
    finally:
        await close(c)
