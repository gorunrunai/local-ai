"""Benchmark the local stack.

    uv run python scripts/benchmark.py [--models gemma-4-12b qwen3.5-35b-a3b] [--runs 3]

Reports: LLM time-to-first-token and decode tok/s (short and ~4K-token prompts), STT
real-time factor, TTS time-to-first-audio, video preprocessing time per minute of footage,
embedding throughput, and peak memory with all default models loaded. Results are printed
and saved to data/bench/<timestamp>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inference.config import ROOT
from inference.manager import ModelManager
from inference.types import ChatRequest, Usage
from media.cache import MediaCache
from media.router import ModalityRouter
from media.types import Attachment

FIX = ROOT / "tests" / "fixtures"
LONG_PROMPT = "\n".join(f"Line {i}: the quick brown fox jumps over the lazy dog." for i in range(350))


async def llm_bench(m: ModelManager, model: str, runs: int) -> dict:
    t0 = time.perf_counter()
    await m.ensure_llm(model)
    load_s = round(time.perf_counter() - t0, 1)
    out: dict = {"load_s": load_s}
    for label, prompt, max_tokens in [("short", "Write a 150-word story about a lighthouse.", 256),
                                      ("prompt_4k", LONG_PROMPT + "\nSummarize in one line.", 64)]:
        ttfts, rates, prompt_tokens = [], [], None
        for i in range(runs):
            # Vary the prompt so no run benefits from a prefix cache.
            content = f"[run {i} {time.time()}]\n{prompt}"
            async for ev in m.provider.chat_stream(ChatRequest(
                    model=model, messages=[{"role": "user", "content": content}],
                    max_tokens=max_tokens, temperature=0.7)):
                if isinstance(ev, Usage):
                    ttfts.append(ev.ttft_s)
                    if ev.decode_tok_s:
                        rates.append(ev.decode_tok_s)
                    prompt_tokens = ev.prompt_tokens
        out[label] = {"ttft_s": round(statistics.median(ttfts), 3),
                      "decode_tok_s": round(statistics.median(rates), 1) if rates else None,
                      "prompt_tokens": prompt_tokens,
                      "prefill_tok_s": round(prompt_tokens / statistics.median(ttfts), 0)
                      if prompt_tokens and ttfts else None}
    status = await m.status()
    out["memory_gb"] = next(l["memory_gb"] for l in status["llms"] if l["id"] == model)
    return out


async def stt_bench(m: ModelManager) -> dict:
    stt = m.stt()
    await stt.ensure_loaded()
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        from media.ffmpeg import to_wav16k

        tour = await to_wav16k(FIX / "tour.mp4", Path(tmp) / "tour.wav")
        for name, path in [("dictation_6s", FIX / "dictation.wav"), ("narration_75s", tour)]:
            tr = await stt.transcribe(path)
            res[name] = {"rtf": tr.rtf, "elapsed_s": tr.elapsed_s, "audio_s": round(tr.duration_s, 1)}
    return res


async def tts_bench(m: ModelManager) -> dict:
    tts = m.tts()
    t0 = time.perf_counter()
    await tts.ensure_loaded()
    load_s = round(time.perf_counter() - t0, 2)
    firsts = []
    for text in ["Sure, here is what I found about your question.",
                 "The weather in Paris is mild today, around eighteen degrees.",
                 "Let me think about that for a second."]:
        chunk = await tts.synthesize(text)
        firsts.append(chunk.elapsed_s)
        audio_s = len(chunk.samples) / chunk.sample_rate
    return {"load_s": load_s, "time_to_first_audio_s": round(statistics.median(firsts), 3),
            "last_sentence_audio_s": round(audio_s, 2)}


async def video_bench(m: ModelManager) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        router = ModalityRouter(m.stt, MediaCache(Path(tmp)))
        spec = m.llm_spec()
        t0 = time.perf_counter()
        p = await router.prepare(Attachment(FIX / "tour.mp4"), spec)
        total = time.perf_counter() - t0
        t1 = time.perf_counter()
        await router.prepare(Attachment(FIX / "tour.mp4"), spec)
        cached = time.perf_counter() - t1
    minutes = p.meta["duration_s"] / 60
    return {"clip_s": p.meta["duration_s"], "frames": len(p.meta.get("frames", [])),
            "total_s": round(total, 2), "s_per_minute": round(total / minutes, 2),
            "cached_s": round(cached, 3), "breakdown": p.meta["timings"]}


async def embed_bench(m: ModelManager) -> dict:
    emb = m.embedder()
    t0 = time.perf_counter()
    await emb.ensure_loaded()
    load_s = time.perf_counter() - t0
    texts = [f"Document {i}: local assistants keep data on the device." for i in range(256)]
    t1 = time.perf_counter()
    vecs = await emb.embed(texts)
    dt = time.perf_counter() - t1
    rr = m.reranker()
    t2 = time.perf_counter()
    if rr:
        await rr.rerank("where is data kept?", texts[:32])
    return {"load_s": round(load_s, 2), "texts_per_s": round(len(texts) / dt, 1), "dims": vecs.shape[1],
            "rerank_32_s": round(time.perf_counter() - t2, 3) if rr else None}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    m = ModelManager()
    await m.start()
    results: dict = {"llm": {}}
    peak = 0.0
    try:
        for model in args.models or [m.cfg.defaults.llm]:
            print(f"… LLM {model}", flush=True)
            results["llm"][model] = await llm_bench(m, model, args.runs)
        await m.ensure_llm(m.cfg.defaults.llm)
        print("… STT", flush=True)
        results["stt"] = await stt_bench(m)
        print("… TTS", flush=True)
        results["tts"] = await tts_bench(m)
        print("… video preprocessing", flush=True)
        results["video"] = await video_bench(m)
        print("… embeddings", flush=True)
        results["embeddings"] = await embed_bench(m)
        # Peak: every model resident at once (default LLM + audio listener + all services).
        listener = m.cfg.defaults.audio_listener
        if listener:
            print(f"… loading {listener} alongside for the all-models peak", flush=True)
            await m.ensure_llm(listener)
        await m.reranker().ensure_loaded()
        status = await m.status()
        peak = status["models_total_gb"]
        results["memory"] = {"all_models_loaded_gb": peak,
                             "backend_process_gb": status["backend_process_gb"],
                             "llm_gb": {l["id"]: l["memory_gb"] for l in status["llms"] if l["loaded"]},
                             "system": status["system"], "budget_gb": status["budget_gb"]}
    finally:
        await m.stop()
    out_dir = ROOT / "data" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"\nsaved {out}")


if __name__ == "__main__":
    asyncio.run(main())
