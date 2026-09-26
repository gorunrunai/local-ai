"""Scripted voice-mode round trip (acceptance test 6) — also a latency benchmark.

    uv run python scripts/voice_roundtrip.py [--wav tests/fixtures/voice_question.wav] [--runs 3]

Streams a WAV to /api/voice in real time (32 ms frames), then silence, and measures the time
from the last frame of speech to the first byte of reply audio, plus the server's breakdown.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

FRAME = 512


def load_pcm16(path: Path) -> np.ndarray:
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        import subprocess

        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype="<i2")
    return (np.clip(audio, -1, 1) * 32767).astype("<i2")


async def one_turn(url: str, pcm: np.ndarray, timeout: float = 60, barge_pcm: np.ndarray | None = None,
                   silence_s: float | None = None) -> dict:
    out: dict = {"events": [], "audio_bytes": 0}
    # Incognito chat: benchmark turns aren't saved to your chat history.
    api = url.replace("ws://", "http://").replace("wss://", "https://").rsplit("/voice", 1)[0]
    import httpx

    async with httpx.AsyncClient() as http:
        cid = (await http.post(f"{api}/conversations", json={"incognito": True})).json()["id"]
    url = f"{url}{'&' if '?' in url else '?'}conversation_id={cid}"
    async with websockets.connect(url, max_size=None) as ws:
        ready = json.loads(await ws.recv())
        out["conversation_id"] = ready["conversation_id"]
        speech_end_sent = None
        first_audio = None
        done = asyncio.Event()

        async def sender():
            nonlocal speech_end_sent
            t0 = time.monotonic()
            frames = [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)]
            for i, f in enumerate(frames):  # real-time pacing
                await ws.send(f.tobytes())
                await asyncio.sleep(max(0, t0 + (i + 1) * 0.032 - time.monotonic()))
            speech_end_sent = time.monotonic()
            silence = np.zeros(FRAME, dtype="<i2").tobytes()
            t_sil = time.monotonic()
            while not done.is_set():  # keep the mic "open" with silence
                if silence_s is not None and time.monotonic() - t_sil > silence_s:
                    await asyncio.sleep(0.05)
                    continue
                await ws.send(silence)
                await asyncio.sleep(0.032)

        async def receiver():
            nonlocal first_audio
            barge_sent = False
            async for msg in ws:
                if isinstance(msg, bytes):
                    out["audio_bytes"] += len(msg)
                    if first_audio is None:
                        first_audio = time.monotonic()
                    if barge_pcm is not None and not barge_sent:
                        barge_sent = True
                        out["barge_sent_at"] = time.monotonic()
                        for i in range(0, len(barge_pcm), FRAME):
                            await ws.send(barge_pcm[i:i + FRAME].tobytes())
                            await asyncio.sleep(0.032)
                    continue
                ev = json.loads(msg)
                out["events"].append(ev)
                if ev["type"] == "metrics":
                    out.setdefault("metrics", ev)
                if ev["type"] == "interrupted":
                    out["interrupted_after_s"] = time.monotonic() - out.get("barge_sent_at", time.monotonic())
                if ev["type"] == "turn_done" and (barge_pcm is None or out.get("interrupted_after_s") is not None):
                    done.set()
                    return

        s = asyncio.create_task(sender())
        try:
            await asyncio.wait_for(receiver(), timeout)
        finally:
            done.set()
            s.cancel()
    out["client_latency_ms"] = round((first_audio - speech_end_sent) * 1000) if first_audio and speech_end_sent else None
    out["final_metrics"] = next((e for e in reversed(out["events"]) if e["type"] == "metrics"), {})
    out["transcript"] = next((e["text"] for e in out["events"] if e["type"] == "final_transcript"), None)
    out["reply"] = "".join(e["text"] for e in out["events"] if e["type"] == "assistant_delta")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/api/voice")
    ap.add_argument("--wav", default="tests/fixtures/voice_question.wav")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--silence-s", type=float, default=None, help="stop sending silence after N s")
    args = ap.parse_args()
    pcm = load_pcm16(Path(args.wav))
    lat = []
    for i in range(args.runs):
        r = await one_turn(args.url, pcm, silence_s=args.silence_s)
        m = r.get("final_metrics") or r.get("metrics", {})
        lat.append(r["client_latency_ms"])
        print(f"run {i + 1}: end-of-speech → first audio {r['client_latency_ms']} ms (client) / "
              f"{m.get('end_of_speech_to_first_audio_ms')} ms (server)  | turn wait {m.get('turn_wait_ms')} ms, "
              f"STT {m.get('stt_ms')} ms, turn setup {m.get('message_start_ms')}/{m.get('prompt_ready_ms')} ms, "
              f"LLM first text {m.get('llm_first_text_ms')} ms (model TTFT {m.get('llm_ttft_ms')} ms, "
              f"{m.get('prompt_tokens')} prompt tokens), TTS {m.get('tts_first_ms')} ms"
              f"\n       heard: {r['transcript']!r}\n       reply: {r['reply'][:120]!r}")
    ok = [x for x in lat if x is not None]
    if ok:
        print(f"median {statistics.median(ok):.0f} ms, best {min(ok)} ms, worst {max(ok)} ms (target < 2000 ms)")


if __name__ == "__main__":
    asyncio.run(main())
