"""Full-duplex voice sessions over a WebSocket.

Protocol (`/api/voice?mode=conversation|dictation&conversation_id=…&voice=…&speed=…&sensitivity=…`):

  client → server
    binary   PCM16 mono 16 kHz audio, any chunk size (sent continuously while the mic is on)
    {"type": "interrupt"}       stop the assistant now (e.g. tap on the orb)
    {"type": "playback_done"}   the client finished playing all audio it received
    {"type": "stop"}            end the session (dictation: finalize the current utterance)

  server → client
    {"type": "ready", "conversation_id", "sample_rate": 16000}
    {"type": "state", "state": "loading|listening|user_speaking|processing|thinking|speaking"}
    {"type": "loading", "step", "message"}            what's loading before the first "listening"
    {"type": "partial_transcript", "text"}            live, while the user talks
    {"type": "final_transcript", "text", "turn"}      when the turn ends
    {"type": "turn_started", "user_message_id", "assistant_message_id", "conversation_id"}
    {"type": "assistant_delta", "text"}               reply text as it streams
    {"type": "tool", "name", "status"}                tool use during a spoken reply
    {"type": "audio", "seq", "text", "sample_rate", "duration_s"} followed by one binary frame
    {"type": "confirm", "id", "name", "arguments"}      a tool waits for Allow / Deny
    {"type": "tool_done", "id", "name", "ok", "files"}  a tool finished (files: videos, images…)
    {"type": "artifact", "artifact"}                   an artifact was created or updated
                                                      of PCM16 mono audio (Kokoro, 24 kHz)
    {"type": "interrupted", "reason"}                 client must stop playback immediately
    {"type": "metrics", …}                            per-turn latency breakdown
    {"type": "turn_done", "status"}                   reply finished (audio may still be playing)
    {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.chat import AppState, ChatService, TurnRequest
from orchestrator.voice.detectors import SileroVAD, float_to_pcm16, pcm16_to_float, smart_turn
from orchestrator.voice.speech import SentenceChunker, while_busy
from orchestrator.voice.turns import FRAME_SAMPLES, TurnConfig, TurnEvent, TurnTracker

log = logging.getLogger(__name__)
FRAME_BYTES = FRAME_SAMPLES * 2
PARTIAL_EVERY_S = float(__import__("os").environ.get("VOICE_PARTIAL_EVERY_S", "0.6"))
SMART_TURN_COMPLETE = 0.5


@dataclass
class Reply:
    speech_end: float                  # monotonic time the user stopped speaking
    cancelled: bool = False
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    spoken: list[str] = field(default_factory=list)
    assistant_message_id: str | None = None
    first_text_at: float | None = None
    first_audio_at: float | None = None
    metrics: dict = field(default_factory=dict)
    hushed: bool = False               # the user started talking: stay quiet until we know what they said
    tool: str | None = None            # the tool running (or awaiting approval) right now
    deferred: str | None = None        # a request made while a tool ran, answered right after

BUSY_LINE = "Still working on that. I'll answer you right after."


class VoiceSession:
    def __init__(self, state: AppState, ws: WebSocket, *, mode: str, conversation_id: str | None,
                 voice: str | None, speed: float, sensitivity: float):
        self.s = state
        self.ws = ws
        self.mode = mode
        self.cid = conversation_id
        self.voice = voice
        self.speed = speed
        self.vad = SileroVAD()
        self.turns = TurnTracker(TurnConfig.from_sensitivity(sensitivity))
        self.smart = smart_turn()
        self.pending = b""
        self.reply: Reply | None = None
        self.reply_task: asyncio.Task | None = None
        self.partial_task: asyncio.Task | None = None
        self.last_partial = 0.0
        self.play_until = 0.0
        self.turn_no = 0
        self.metrics_log: list[dict] = []
        self._send_lock = asyncio.Lock()
        self._state = ""
        self._closed = False
        self._warmup: asyncio.Task | None = None
        self._ready = False        # audio is ignored until everything the first turn needs is loaded

    # --- plumbing ---------------------------------------------------------------------
    async def send(self, obj: dict, audio: bytes | None = None) -> None:
        if self._closed:
            return
        async with self._send_lock:  # keep an audio header and its payload adjacent
            try:
                await self.ws.send_text(json.dumps(obj))
                if audio is not None:
                    await self.ws.send_bytes(audio)
            except (WebSocketDisconnect, RuntimeError):
                self._closed = True

    async def set_state(self, st: str) -> None:
        if st != self._state:
            self._state = st
            await self.send({"type": "state", "state": st})

    @property
    def assistant_active(self) -> bool:
        return self.mode == "conversation" and (
            (self.reply_task is not None and not self.reply_task.done()) or time.monotonic() < self.play_until)

    # --- main loop ------------------------------------------------------------------------
    async def run(self) -> None:
        if self.mode == "conversation":
            await self._ensure_conversation()
        await self.send({"type": "ready", "conversation_id": self.cid, "sample_rate": 16000,
                         "turn_detection": "smart-turn" if self.smart else "silence"})
        # Say "listening" only once a reply can actually happen. The first start loads speech
        # recognition, the voice and the chat model (tens of seconds); until then, say so.
        self._warmup = asyncio.create_task(self._get_ready())
        try:
            while not self._closed:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self.on_audio(msg["bytes"])
                elif msg.get("text") and await self.on_control(json.loads(msg["text"])):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        await self.interrupt("session_end", notify=False)
        for t in (self.reply_task, self.partial_task):
            if t and not t.done():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(t, 15)
        self._closed = True

    async def _ensure_conversation(self) -> None:
        store = self.s.store_for(self.cid)
        if not self.cid or not await store.get_conversation(self.cid):
            conv = await self.s.persistent.create_conversation()
            self.cid = conv["id"]

    async def on_control(self, msg: dict) -> bool:
        kind = msg.get("type")
        if kind == "interrupt":
            await self.interrupt("user")
        elif kind == "playback_done":
            self.play_until = 0.0
            if not (self.reply_task and not self.reply_task.done()):
                await self.set_state("listening")
        elif kind == "stop":
            if self.mode == "dictation" and self.turns.s.in_speech:
                await self.end_turn()
            return True
        return False

    async def _needs_loading(self) -> bool:
        m = self.s.manager
        return (not m.stt().loaded or not m.tts().loaded
                or m.cfg.defaults.llm not in await m.running_llms())

    async def _get_ready(self) -> None:
        """Load (and prime) what the first turn needs, reporting each step, then start listening.

        Dictation records from the start (speech recognition loads while you talk); a conversation
        only listens once a reply can actually happen."""
        m = self.s.manager
        if self.mode != "conversation":
            self._ready = True
            await self.set_state("listening")
            with contextlib.suppress(Exception):
                await m.stt().ensure_loaded()
            return
        try:
            if await self._needs_loading():
                await self.set_state("loading")
                steps = [("speech", "Loading speech recognition", m.stt().ensure_loaded),
                         ("voice", "Loading the voice", m.tts().ensure_loaded),
                         # Loads the chat model and primes it with the real instructions and tools,
                         # so the first reply is as quick as later ones.
                         ("model", "Loading the AI model", ChatService(self.s).warm_up)]
                for step, message, work in steps:
                    if self._closed:
                        return
                    await self.send({"type": "loading", "step": step, "message": message})
                    try:
                        await work()
                    except Exception:
                        log.warning("voice warm-up step %s failed", step, exc_info=True)
        finally:
            self._ready = True
            if not self._closed:
                await self.set_state("listening")

    # --- audio in -------------------------------------------------------------------------------
    async def on_audio(self, data: bytes) -> None:
        if not self._ready:        # still loading: nothing can answer yet, so don't collect speech
            self.pending = b""
            return
        buf = self.pending + data
        usable = len(buf) - len(buf) % FRAME_BYTES
        for off in range(0, usable, FRAME_BYTES):
            await self.on_frame(pcm16_to_float(buf[off:off + FRAME_BYTES]))
        self.pending = buf[usable:]

    async def on_frame(self, frame: np.ndarray) -> None:
        prob = self.vad.prob(frame)
        ev = self.turns.feed(frame, prob, assistant_speaking=self.assistant_active)
        if ev is None:
            return
        if ev in (TurnEvent.SPEECH_START, TurnEvent.BARGE_IN):
            if ev is TurnEvent.BARGE_IN:
                await self.hush()
            self.last_partial = time.monotonic()
            await self.set_state("user_speaking")
        elif ev is TurnEvent.SPEECH_CONTINUE:
            self._maybe_partial()
        elif ev is TurnEvent.PAUSE:
            if self.smart is None:
                return
            audio = np.concatenate(self.turns.s.frames)
            p = await asyncio.to_thread(self.smart.complete_prob, audio)
            # Still paused (no new speech arrived while we were deciding)?
            if p >= SMART_TURN_COMPLETE and self.turns.s.in_speech and self.turns.s.silence_frames > 0:
                await self.end_turn(smart_p=p)
        elif ev is TurnEvent.TURN_END:
            await self.end_turn()

    def _maybe_partial(self) -> None:
        now = time.monotonic()
        if now - self.last_partial < PARTIAL_EVERY_S or (self.partial_task and not self.partial_task.done()):
            return
        self.last_partial = now
        audio = np.concatenate(self.turns.s.frames)

        async def partial():
            try:
                tr = await self.s.manager.stt().transcribe_array(audio)
                if tr.text and self.turns.s.in_speech:
                    await self.send({"type": "partial_transcript", "text": tr.text})
            except Exception:
                log.debug("partial transcript failed", exc_info=True)

        self.partial_task = asyncio.create_task(partial())

    async def end_turn(self, smart_p: float | None = None) -> None:
        silence_ms = self.turns.speech_end_offset_ms()
        speech_end = time.monotonic() - silence_ms / 1000
        audio = np.concatenate(self.turns.s.frames)
        self.turns.reset()
        self.vad.reset()
        self.turn_no += 1
        await self.set_state("processing")
        t0 = time.monotonic()
        tr = await self.s.manager.stt().transcribe_array(audio)
        text = tr.text.strip()
        metrics = {"turn": self.turn_no, "turn_wait_ms": round(silence_ms), "smart_turn_p": smart_p,
                   "stt_ms": round((time.monotonic() - t0) * 1000), "utterance_s": round(len(audio) / 16000, 2)}
        if not text:
            if self.reply is not None and self.reply.hushed:   # just noise: carry on
                self.reply.hushed = False
            await self.set_state("listening" if not (self.reply_task and not self.reply_task.done()) else "thinking")
            return
        await self.send({"type": "final_transcript", "text": text, "turn": self.turn_no})
        if self.mode == "dictation":
            await self.set_state("listening")
            return
        r = self.reply
        if r is not None and not r.cancelled and self.reply_task and not self.reply_task.done():
            # The user spoke while the assistant was still working on the last request.
            kind = while_busy(text)
            if kind == "stop":
                await self.interrupt("user")
                await self.set_state("listening")
                return
            if kind == "ack" or r.tool:
                r.hushed = False
                if kind == "other":                   # e.g. a question during a video render
                    r.deferred = text if not r.deferred else f"{r.deferred} {text}"
                    r.queue.put_nowait(BUSY_LINE)
                await self.set_state("thinking")
                return
            await self.interrupt("barge_in")
        elif r is not None and r.hushed:              # the reply had finished; only its audio was cut
            r.hushed = False
        if self.reply_task and not self.reply_task.done():  # an interrupted reply still saving
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self.reply_task), 10)
        self.reply = Reply(speech_end=speech_end, metrics=metrics)
        self.reply_task = asyncio.create_task(self._reply(self.reply, text))

    # --- reply out ----------------------------------------------------------------------------
    async def _reply(self, r: Reply, text: str) -> None:
        await self.set_state("thinking")
        chunker = SentenceChunker()
        tts_task = asyncio.create_task(self._speak(r))
        t_start = time.monotonic()
        status = "complete"
        try:
            async for event, data in ChatService(self.s).run_turn(TurnRequest(self.cid, text, voice=True)):
                if r.cancelled and event not in ("message_end",):
                    continue
                if event in ("message_start", "prompt_ready"):
                    r.metrics[f"{event}_ms"] = round((time.monotonic() - t_start) * 1000)
                if event == "message_start":
                    r.assistant_message_id = data["assistant_message_id"]
                    await self.send({"type": "turn_started", "conversation_id": data["conversation_id"],
                                     "user_message_id": data["user_message"]["id"],
                                     "assistant_message_id": data["assistant_message_id"]})
                elif event == "text_delta":
                    if r.first_text_at is None:
                        r.first_text_at = time.monotonic()
                        r.metrics["llm_first_text_ms"] = round((r.first_text_at - t_start) * 1000)
                    await self.send({"type": "assistant_delta", "text": data["text"]})
                    for sentence in chunker.feed(data["text"]):
                        r.queue.put_nowait(sentence)
                elif event == "tool_call":
                    if data.get("status") == "running":
                        r.tool = data["name"]
                    await self.send({"type": "tool", "name": data["name"], "status": data["status"]})
                elif event == "tool_confirmation":
                    r.tool = data["name"]
                    # The chat (with its Allow / Deny card) is hidden behind voice mode: ask there too.
                    await self.send({"type": "confirm", "id": data["id"], "name": data["name"],
                                     "arguments": data.get("arguments") or {}})
                elif event == "tool_result":
                    r.tool = None
                    await self.send({"type": "tool_done", "id": data["id"], "name": data["name"], "ok": data["ok"],
                                     "files": data.get("files") or []})
                elif event == "artifact":
                    await self.send({"type": "artifact", "artifact": {k: data.get(k) for k in (
                        "id", "identifier", "type", "title", "version", "language")}})
                elif event == "usage":
                    r.metrics["llm_ttft_ms"] = round((data.get("ttft_s") or 0) * 1000)
                    r.metrics["prompt_tokens"] = data.get("prompt_tokens")
                elif event == "error":
                    await self.send({"type": "error", "message": data.get("message", "error")})
                elif event == "message_end":
                    status = data.get("status", status)
            if not r.cancelled:
                for sentence in chunker.flush():
                    r.queue.put_nowait(sentence)
        finally:
            r.queue.put_nowait(None)
            with contextlib.suppress(Exception):
                await tts_task
            if r.cancelled:
                status = "interrupted"
                await self._save_spoken(r)
            await self.send({"type": "metrics", "final": True, **r.metrics})
            await self.send({"type": "turn_done", "status": status})
            if r.deferred and not r.cancelled and not self._closed and self.reply is r:
                # Something the user asked while a tool was running: answer it now.
                self.reply = Reply(speech_end=time.monotonic(), metrics={"turn": self.turn_no, "deferred": True})
                self.reply_task = asyncio.create_task(self._reply(self.reply, r.deferred))
            elif not r.cancelled and time.monotonic() >= self.play_until:
                await self.set_state("listening")

    async def _speak(self, r: Reply) -> None:
        tts = self.s.manager.tts()
        seq = 0
        while True:
            sentence = await r.queue.get()
            if sentence is None or r.cancelled:
                return
            if r.hushed:                        # the user is talking: don't talk over them
                continue
            t0 = time.monotonic()
            chunk = await tts.synthesize(sentence, self.voice, self.speed)
            if r.cancelled or self._closed:
                return
            duration = len(chunk.samples) / chunk.sample_rate
            if r.first_audio_at is None:
                r.first_audio_at = time.monotonic()
                r.metrics.update({
                    "tts_first_ms": round((r.first_audio_at - t0) * 1000),
                    "first_sentence_chars": len(sentence),
                    "end_of_speech_to_first_audio_ms": round((r.first_audio_at - r.speech_end) * 1000),
                })
                self.metrics_log.append(r.metrics)
                await self.send({"type": "metrics", **r.metrics})
                await self.set_state("speaking")
            now = time.monotonic()
            self.play_until = max(self.play_until, now) + duration
            seq += 1
            await self.send({"type": "audio", "seq": seq, "text": sentence, "sample_rate": chunk.sample_rate,
                             "duration_s": round(duration, 3)}, float_to_pcm16(chunk.samples))
            r.spoken.append(sentence)

    async def hush(self) -> None:
        """The user started talking over the assistant: stop its audio now, but keep the reply (and any
        tool it's running) going until we know what they said. See end_turn."""
        r = self.reply
        active = self.assistant_active
        if r is None or r.cancelled or not active:
            return
        r.hushed = True
        while not r.queue.empty():
            r.queue.get_nowait()
        self.play_until = 0.0
        await self.send({"type": "interrupted", "reason": "barge_in"})

    async def interrupt(self, reason: str, notify: bool = True) -> None:
        r = self.reply
        active = self.assistant_active
        if r is not None and not r.cancelled and (self.reply_task and not self.reply_task.done() or active):
            r.cancelled = True
            while not r.queue.empty():
                r.queue.get_nowait()
            if ev := self.s.runs.get(self.cid or ""):
                ev.set()
        self.play_until = 0.0
        if notify and (active or (r is not None and r.cancelled)):
            await self.send({"type": "interrupted", "reason": reason})

    async def _save_spoken(self, r: Reply) -> None:
        """What the user actually heard becomes the stored reply (the model sees it next turn)."""
        if not r.assistant_message_id:
            return
        store = self.s.store_for(self.cid)
        heard = " ".join(r.spoken).strip()
        content = f"{heard} … [interrupted by the user]" if heard else "[interrupted by the user before speaking]"
        with contextlib.suppress(Exception):
            await store.update_message(r.assistant_message_id, content=content, status="stopped",
                                       blocks=[{"type": "text", "text": content}])
