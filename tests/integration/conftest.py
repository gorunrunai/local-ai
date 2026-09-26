"""Fixtures for integration tests against the real local models.

Run with `make test-integration` (starts llama-swap + the default model on demand).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from inference.manager import ModelManager
from inference.types import (
    ChatRequest,
    ReasoningDelta,
    StreamError,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from media.cache import MediaCache
from media.compose import DATA_ENVELOPE_RULES, build_user_content
from media.router import ModalityRouter, RouteOptions
from media.types import Attachment

FIX = Path(__file__).resolve().parent.parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}/api"


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((FIX / "expected.json").read_text())


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def manager():
    m = ModelManager()
    await m.start()
    await m.ensure_llm()
    yield m
    await m.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def router(manager, tmp_path_factory):
    return ModalityRouter(manager.stt, MediaCache(tmp_path_factory.mktemp("media-cache")))


class Asker:
    def __init__(self, manager: ModelManager, router: ModalityRouter):
        self.m, self.r = manager, router
        self.last_usage: Usage | None = None

    async def __call__(self, question: str, files: list[tuple[str, str]] = (),
                       model: str | None = None, opts: RouteOptions | None = None,
                       system: str | None = None, max_tokens: int = 400,
                       tools: list[dict] | None = None) -> dict:
        model = model or self.m.cfg.defaults.llm
        spec = self.m.llm_spec(model)
        atts = [Attachment(FIX / name, source=src) for name, src in files]
        prepared = await self.r.prepare_all(atts, spec, opts)
        messages = [{"role": "system", "content": system or DATA_ENVELOPE_RULES},
                    {"role": "user", "content": build_user_content(question, prepared)}]
        text, reasoning, calls = [], [], {}
        async for ev in self.m.provider.chat_stream(ChatRequest(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.0, tools=tools)):
            if isinstance(ev, TextDelta):
                text.append(ev.text)
            elif isinstance(ev, ReasoningDelta):
                reasoning.append(ev.text)
            elif isinstance(ev, ToolCallDelta):
                slot = calls.setdefault(ev.index, {"name": "", "arguments": ""})
                slot["name"] += ev.name or ""
                slot["arguments"] += ev.arguments
            elif isinstance(ev, StreamError):
                print(f"\n  STREAM ERROR: {ev.status} {ev.message[:500]}")
            elif isinstance(ev, Usage):
                self.last_usage = ev
                self.m.record_usage(model, ev)
        u = self.last_usage
        print(f"\n[{model}] Q: {question}\n  A: {''.join(text).strip()}\n  R: {''.join(reasoning)[:300]!r}\n  "
              f"(prompt={u and u.prompt_tokens} ttft={u and u.ttft_s}s tok/s={u and u.decode_tok_s})")
        return {"text": "".join(text), "reasoning": "".join(reasoning),
                "tool_calls": list(calls.values()), "prepared": prepared}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ask(manager, router) -> Asker:
    return Asker(manager, router)


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("appdata")
    env = {**os.environ, "DATA_DIR": str(data), "APP_PORT": str(PORT), "WEB_FETCH_ALLOW_PRIVATE_FOR_TESTS": "1"}
    log = open(data / "server.log", "wb")  # noqa: SIM115 - lives as long as the server
    proc = subprocess.Popen(["uv", "run", "uvicorn", "orchestrator.app:app", "--host", "127.0.0.1",
                             "--port", str(PORT), "--no-proxy-headers"],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        proc.kill()
        pytest.fail("backend did not start; see " + str(data / "server.log"))
    # wait for the default model to be warm
    httpx.post(f"{BASE}/models/qwen3.5-35b-a3b/load", timeout=300)
    # Evaluation mode: low temperature so acceptance tests measure typical behaviour, not sampling luck.
    httpx.patch(f"{BASE}/settings", json={"temperature": 0.2}, timeout=30)
    yield data
    proc.terminate()
    proc.wait(30)
    log.close()


