"""Provider normalization, swap config generation, memory budget and config validation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from inference.config import LLMSpec, load_config
from inference.manager import BudgetExceeded, ModelManager
from inference.providers.openai_compat import (
    OpenAICompatProvider,
    build_body,
    encode_part,
    parse_sse_event,
)
from inference.swap import build_swap_config
from inference.types import (
    ChatRequest,
    Done,
    ReasoningDelta,
    StreamError,
    TextDelta,
    ToolCallDelta,
    Usage,
)


# --- content-part encoding ----------------------------------------------------------------
def test_audio_part_base64(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFFxxxxWAVE")
    part = encode_part({"type": "input_audio", "input_audio": {"path": str(f)}})
    assert part["input_audio"]["format"] == "wav" and part["input_audio"]["data"]


def test_video_part_is_a_local_path(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\x00" * 8)
    canonical = {"type": "input_video", "input_video": {"path": str(f)}}
    assert encode_part(canonical) == {"type": "video_url", "video_url": {"url": str(f)}}


def test_thinking_flag_and_budget():
    req = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}], thinking=True,
                      thinking_budget=256, max_tokens=5)
    body = build_body(req)
    assert body["enable_thinking"] is True and body["thinking_budget"] == 256 and body["max_tokens"] == 5
    assert "temperature" not in body
    off = build_body(ChatRequest(model="m", messages=[], thinking=False, thinking_budget=256))
    assert off["enable_thinking"] is False and "thinking_budget" not in off


# --- SSE parsing --------------------------------------------------------------------------
def test_parse_reasoning_both_field_names():
    a = parse_sse_event({"choices": [{"delta": {"reasoning": "hmm"}}]})
    b = parse_sse_event({"choices": [{"delta": {"reasoning_content": "hmm"}}]})
    assert a == b == [ReasoningDelta("hmm")]


def test_parse_tool_call_with_dict_arguments():
    ev = parse_sse_event({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "f", "arguments": {"x": 1}}}]}}]})
    assert ev == [ToolCallDelta(index=0, id="c1", name="f", arguments='{"x": 1}')]


def test_parse_error_payload():
    assert isinstance(parse_sse_event({"error": {"message": "boom"}})[0], StreamError)


def _sse(*payloads) -> bytes:
    return b"".join(f"data: {json.dumps(p)}\n\n".encode() for p in payloads) + b"data: [DONE]\n\n"


async def test_stream_normalizes_events():
    body = _sse(
        {"choices": [{"delta": {"reasoning_content": "think "}}]},
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t", "function": {"name": "get", "arguments": "{\"a\""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 4}},
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    p = OpenAICompatProvider("http://x", transport=httpx.MockTransport(handler))
    events = [e async for e in p.chat_stream(ChatRequest(model="m", messages=[]))]
    await p.aclose()
    assert seen["body"]["stream"] is True and "enable_thinking" in seen["body"]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    assert "".join(e.arguments for e in events if isinstance(e, ToolCallDelta)) == '{"a":1}'
    usage = next(e for e in events if isinstance(e, Usage))
    assert usage.prompt_tokens == 7 and usage.ttft_s is not None
    assert isinstance(events[-1], Done) and events[-1].finish_reason == "tool_calls"


async def test_stream_http_error():
    p = OpenAICompatProvider("http://x",
                             transport=httpx.MockTransport(lambda r: httpx.Response(500, text="oom")))
    events = [e async for e in p.chat_stream(ChatRequest(model="m", messages=[]))]
    await p.aclose()
    assert isinstance(events[0], StreamError) and events[0].status == 500 and "oom" in events[0].message


# --- swap config / budget -----------------------------------------------------------------
def config_with_extra():
    """The shipped config plus a model outside every co-residency set, with no prefix cache."""
    cfg = load_config()
    cfg.llms["extra-8b"] = LLMSpec(id="extra-8b", display_name="Extra 8B", repo="mlx-community/extra-8b-4bit",
                                   est_memory_gb=6, prefix_cache_gb=0)
    return cfg


def test_swap_config_is_loopback_only():
    cfg = config_with_extra()
    out = build_swap_config(cfg, python="/py")
    for entry in out["models"].values():
        assert "--host 127.0.0.1" in entry["cmd"] and "${PORT}" in entry["cmd"]
        assert "HF_HUB_OFFLINE=1" in entry["env"]
    assert "mlx_vlm.server" in out["models"]["extra-8b"]["cmd"]
    assert out["models"]["gemma-4-12b"]["useModelName"] == cfg.llms["gemma-4-12b"].repo


def test_prefix_cache_is_ram_only():
    out = build_swap_config(config_with_extra(), python="/py")
    env = out["models"]["qwen3.5-35b-a3b"]["env"]
    assert "APC_ENABLED=1" in env and "APC_DISK_ENABLED=0" in env
    assert not any(e.startswith("APC_") for e in out["models"]["extra-8b"]["env"])   # prefix_cache_gb: 0


def test_config_rejects_public_bind(tmp_path):
    import yaml

    raw = yaml.safe_load(Path("config/models.yaml").read_text())
    raw["proxy"]["listen"] = "0.0.0.0:8090"
    f = tmp_path / "m.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="loopback"):
        load_config(f)


class _Svc:
    def __init__(self, gb, last):
        self.spec = type("S", (), {"est_memory_gb": gb, "id": f"s{gb}"})()
        self._last_used = last


def test_budget_evicts_lru_services_first():
    m = ModelManager(load_config(), start_proxy=False)
    q = m.cfg.llms["qwen3.5-35b-a3b"]
    m.cfg.memory_budget_gb = q.est_memory_gb + q.prefix_cache_gb + 3
    old, new = _Svc(3, last=1), _Svc(2, last=9)
    projected, evict = m.plan_budget("qwen3.5-35b-a3b", [new, old])
    assert evict == [old] and projected == q.est_memory_gb + q.prefix_cache_gb + 2


def test_budget_counts_co_resident_partner():
    m = ModelManager(config_with_extra(), start_proxy=False)
    m.cfg.memory_budget_gb = 45
    projected, _ = m.plan_budget("gemma-4-12b", [], running_llms=["qwen3.5-35b-a3b"])
    g, q = m.cfg.llms["gemma-4-12b"], m.cfg.llms["qwen3.5-35b-a3b"]
    assert projected == g.est_memory_gb + g.prefix_cache_gb + q.est_memory_gb + q.prefix_cache_gb
    # A model outside the set evicts Qwen, so Qwen doesn't count.
    projected, _ = m.plan_budget("extra-8b", [], running_llms=["qwen3.5-35b-a3b"])
    assert projected == m.cfg.llms["extra-8b"].est_memory_gb


def test_budget_refuses_impossible_load():
    m = ModelManager(load_config(), start_proxy=False)
    m.cfg.memory_budget_gb = 10
    with pytest.raises(BudgetExceeded):
        m.plan_budget("qwen3.5-35b-a3b", [])


def test_video_request_folds_system_prompt_into_the_first_turn(tmp_path):
    from inference.providers.openai_compat import encode_messages

    f = tmp_path / "v.mp4"
    f.write_bytes(b"\x00")
    msgs = [{"role": "system", "content": "RULES"},
            {"role": "user", "content": [{"type": "input_video", "input_video": {"path": str(f)}},
                                         {"type": "text", "text": "hi"}]}]
    out = encode_messages(msgs)
    assert out[0]["role"] == "user" and "RULES" in out[0]["content"][0]["text"]
    no_video = [{"role": "system", "content": "RULES"}, {"role": "user", "content": "hi"}]
    assert encode_messages(no_video)[0]["role"] == "system"


def test_local_config_overrides_defaults(tmp_path):
    """install.sh records a Mac's model choice in models.local.yaml, merged over models.yaml."""
    import shutil

    from inference.config import CONFIG_PATH, load_config

    main = tmp_path / "models.yaml"
    shutil.copy(CONFIG_PATH, main)
    base = load_config(main)
    assert base.defaults.llm == "qwen3.5-35b-a3b"
    (tmp_path / "models.local.yaml").write_text(
        "memory_budget_gb: 22\ndefaults:\n  llm: gemma-4-12b\n  title_llm: gemma-4-12b\n")
    cfg = load_config(main)
    assert cfg.defaults.llm == "gemma-4-12b" and cfg.memory_budget_gb == 22
    assert cfg.defaults.stt == base.defaults.stt            # untouched keys survive the merge
    assert cfg.llms["qwen3.5-35b-a3b"].repo == base.llms["qwen3.5-35b-a3b"].repo
