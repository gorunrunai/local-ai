"""Tool-call parser (incl. malformed output) and the policy hook."""

from __future__ import annotations

import pytest

from orchestrator.agent.parser import (
    InlineCallFilter,
    calls_from_deltas,
    calls_from_text,
    cited_indices,
    parse_arguments,
)
from orchestrator.policy import Policy, ToolRegistry
from orchestrator.tools.base import Tool

KNOWN = {"get_weather", "code_exec"}
SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
          "required": ["city"], "additionalProperties": False}


# --- native + text fallbacks -----------------------------------------------------------------
def test_native_deltas_assembled_in_index_order():
    calls = calls_from_deltas({1: {"id": "b", "name": "code_exec", "arguments": "{}"},
                               0: {"id": "a", "name": "get_weather", "arguments": '{"city":"Oslo"}'}})
    assert [c.name for c in calls] == ["get_weather", "code_exec"]


def test_native_delta_without_id_gets_one():
    (c,) = calls_from_deltas({0: {"id": None, "name": "get_weather", "arguments": ""}})
    assert c.id.startswith("call_") and c.arguments == "{}"


def test_tagged_tool_call_in_text():
    text = 'Let me check.\n<tool_call>\n{"name": "get_weather", "arguments": {"city": "Oslo"}}\n</tool_call>'
    calls, cleaned = calls_from_text(text, KNOWN)
    assert calls[0].name == "get_weather" and '"Oslo"' in calls[0].arguments
    assert cleaned == "Let me check."


def test_fenced_json_tool_call():
    text = 'Sure:\n```json\n{"name": "code_exec", "parameters": {"code": "print(1)"}}\n```'
    calls, cleaned = calls_from_text(text, KNOWN)
    assert calls[0].name == "code_exec" and "print(1)" in calls[0].arguments and cleaned == "Sure:"


def test_bare_json_and_openai_function_shape():
    calls, cleaned = calls_from_text('{"function": {"name": "get_weather", "arguments": "{\\"city\\": \\"Rome\\"}"}}', KNOWN)
    assert calls[0].name == "get_weather" and "Rome" in calls[0].arguments and cleaned == ""


def test_unknown_tool_names_are_not_calls():
    text = '```json\n{"name": "rm_rf", "arguments": {}}\n```'
    calls, cleaned = calls_from_text(text, KNOWN)
    assert calls == [] and cleaned == text


def test_plain_json_answer_is_not_a_call():
    calls, _ = calls_from_text('{"city": "Oslo", "temp": 12}', KNOWN)
    assert calls == []


def test_malformed_tagged_json_is_repaired():
    text = "<tool_call>{'name': 'get_weather', 'arguments': {'city': 'Oslo',}}</tool_call>"
    calls, _ = calls_from_text(text, KNOWN)
    assert calls and calls[0].name == "get_weather" and "Oslo" in calls[0].arguments


def test_unterminated_tag_still_parses():
    calls, _ = calls_from_text('<tool_call>{"name": "code_exec", "arguments": {"code": "1+1"}}', KNOWN)
    assert calls and calls[0].name == "code_exec"


# --- argument validation ---------------------------------------------------------------------
def test_valid_arguments():
    assert parse_arguments('{"city": "Oslo", "days": 3}', SCHEMA) == ({"city": "Oslo", "days": 3}, None)


def test_repairable_json():
    args, err = parse_arguments("{'city': 'Oslo', 'days': 2,}", SCHEMA)
    assert err is None and args == {"city": "Oslo", "days": 2}


@pytest.mark.parametrize("raw,fragment", [
    ('{"days": 3}', "'city' is a required property"),
    ('{"city": "Oslo", "days": "three"}', "days: 'three' is not of type 'integer'"),
    ('{"city": "Oslo", "extra": 1}', "Additional properties"),
    ('["Oslo"]', "must be a JSON object"),
])
def test_schema_errors_are_explained(raw, fragment):
    args, err = parse_arguments(raw, SCHEMA)
    assert args is None and fragment in err


def test_empty_arguments_mean_empty_object():
    assert parse_arguments("", {"type": "object"}) == ({}, None)


# --- streaming filter / citations ------------------------------------------------------------
def test_inline_filter_hides_markup_split_across_deltas():
    f = InlineCallFilter()
    shown = f.feed("Checking now <tool") + f.feed('_call>{"name": "x"}') + f.feed(" more") + f.flush()
    assert shown == "Checking now "


def test_inline_filter_passes_lookalikes():
    f = InlineCallFilter()
    assert f.feed("a < b and <tool") + f.feed("s are fine") + f.flush() == "a < b and <tools are fine"


def test_cited_indices():
    assert cited_indices("As shown [2] and [1, 3] and [4–6], not [a] or [100000]") == [2, 1, 3, 4, 5, 6]


# --- policy ---------------------------------------------------------------------------------
class _T(Tool):
    def __init__(self, name, side_effect=False, uses_memory=False, cross=False):
        self.name, self.side_effect, self.uses_memory, self.cross_conversation = name, side_effect, uses_memory, cross


CFG = {"tools": {"web_fetch": {"enabled": True, "policy": "allow"}, "web_search": {"enabled": False},
                 "memory_save": {"enabled": True, "policy": "allow"}}, "mcp": {}}


def test_policy_allows_configured_tool():
    assert Policy(CFG).decide(_T("web_fetch"), {}, incognito=False, memory_enabled=True).action == "allow"


def test_policy_denies_disabled_tool():
    d = Policy(CFG).decide(_T("web_search"), {}, incognito=False, memory_enabled=True)
    assert d.action == "deny" and "turned off" in d.reason


def test_side_effect_tools_confirm_by_default():
    assert Policy(CFG).decide(_T("send_email", side_effect=True), {}, incognito=False,
                              memory_enabled=True).action == "confirm"


def test_user_override_wins_over_config():
    d = Policy(CFG).decide(_T("web_fetch"), {}, incognito=False, memory_enabled=True,
                           overrides={"web_fetch": {"policy": "confirm"}})
    assert d.action == "confirm"


def test_incognito_blocks_memory_and_cross_chat_tools():
    p = Policy(CFG)
    assert p.decide(_T("memory_save", uses_memory=True), {}, incognito=True, memory_enabled=True).action == "deny"
    assert p.decide(_T("conversation_search", cross=True), {}, incognito=True, memory_enabled=True).action == "deny"
    assert p.decide(_T("memory_save", uses_memory=True), {}, incognito=False, memory_enabled=False).action == "deny"
    assert not p.available(_T("memory_save", uses_memory=True), incognito=True, memory_enabled=True)


def test_unknown_tool_denied():
    assert Policy(CFG).decide(None, {}, incognito=False, memory_enabled=True).action == "deny"


def test_mcp_tools_policy_by_read_only_hint():
    from types import SimpleNamespace

    from orchestrator.mcp import MCPTool

    mgr = SimpleNamespace(states={"srv": SimpleNamespace(config={})})
    ro = MCPTool(mgr, "srv", SimpleNamespace(name="lookup", description="", input_schema={},
                                             annotations=SimpleNamespace(read_only_hint=True)))
    rw = MCPTool(mgr, "srv", SimpleNamespace(name="delete", description="", input_schema={}, annotations=None))
    cfg = {"tools": {}, "mcp": {"default_policy": "confirm", "default_policy_read_only": "allow"}}
    assert ro.name == "mcp__srv__lookup"
    assert Policy(cfg).decide(ro, {}, incognito=False, memory_enabled=True).action == "allow"
    assert Policy(cfg).decide(rw, {}, incognito=False, memory_enabled=True).action == "confirm"


def test_registry_lists_builtins(monkeypatch):
    from orchestrator.tools import video_gen, web

    # Optional tools appear only when installed; this Mac's installs mustn't decide the result.
    monkeypatch.setattr(web.searxng, "installed", lambda: True)
    monkeypatch.setattr(video_gen, "installed", lambda spec: (True, ""))
    names = set(ToolRegistry().all())
    assert {"web_fetch", "code_exec", "file_read", "file_search", "memory_save", "memory_search",
            "conversation_search", "create_artifact", "analyze_media", "web_search", "generate_video"} <= names
    # Not installed: hidden.
    monkeypatch.setattr(web.searxng, "installed", lambda: False)
    monkeypatch.setattr(video_gen, "installed", lambda spec: (False, "missing"))
    names = set(ToolRegistry().all())
    assert "web_search" not in names and "generate_video" not in names and "code_exec" in names
