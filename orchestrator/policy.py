"""Tool registry and the policy hook consulted before every tool call."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import yaml

from inference.config import ROOT
from orchestrator.mcp import MCPManager, MCPTool
from orchestrator.tools.base import Tool
from orchestrator.tools.code_exec import CodeExec
from orchestrator.tools.local import (
    AnalyzeMedia,
    ConversationSearch,
    CreateArtifact,
    FileRead,
    FileSearch,
    MemorySave,
    MemorySearch,
)
from orchestrator.tools.video_gen import GenerateVideo
from orchestrator.tools.web import WebFetch, WebSearch

Action = Literal["allow", "confirm", "deny"]


@lru_cache(maxsize=1)
def tools_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "tools.yaml").read_text())


@dataclass
class Decision:
    action: Action
    reason: str = ""


class ToolRegistry:
    def __init__(self, mcp: MCPManager | None = None, config: dict | None = None):
        self.cfg = config or tools_config()
        t = self.cfg["tools"]
        self.builtins: dict[str, Tool] = {tool.name: tool for tool in [
            WebFetch(),
            WebSearch(t.get("web_search", {}).get("searxng_url", "http://127.0.0.1:8888")),
            CodeExec(t.get("code_exec", {}).get("timeout_s", 60)),
            FileRead(), FileSearch(), MemorySave(), MemorySearch(), ConversationSearch(),
            CreateArtifact(), AnalyzeMedia(), GenerateVideo(),
        ] if tool.available()}
        self.mcp = mcp

    def all(self) -> dict[str, Tool]:
        tools = dict(self.builtins)
        if self.mcp:
            tools.update({t.name: t for t in self.mcp.tools()})
        return tools

    def get(self, name: str) -> Tool | None:
        return self.all().get(name)


class Policy:
    """Decides allow / confirm / deny for a tool call.

    Order: hard rules (incognito, disabled) → user override → config → side-effect default.
    """

    def __init__(self, config: dict | None = None):
        self.cfg = config or tools_config()

    def base(self, tool: Tool, overrides: dict | None = None) -> tuple[bool, Action]:
        conf = self.cfg["tools"].get(tool.name, {})
        enabled = conf.get("enabled", True)
        if isinstance(tool, MCPTool):
            mcfg = self.cfg.get("mcp", {})
            policy = tool.mcp_policy or (mcfg.get("default_policy", "confirm") if tool.side_effect
                                         else mcfg.get("default_policy_read_only", "allow"))
        else:
            policy = conf.get("policy", "confirm" if tool.side_effect else "allow")
        o = (overrides or {}).get(tool.name, {})
        return o.get("enabled", enabled), o.get("policy", policy)

    def available(self, tool: Tool, *, incognito: bool, memory_enabled: bool,
                  overrides: dict | None = None) -> bool:
        enabled, action = self.base(tool, overrides)
        if not enabled or action == "deny":
            return False
        if tool.uses_memory and (incognito or not memory_enabled):
            return False
        return not (tool.cross_conversation and incognito)

    def decide(self, tool: Tool | None, args: dict, *, incognito: bool, memory_enabled: bool,
               overrides: dict | None = None) -> Decision:
        if tool is None:
            return Decision("deny", "unknown tool")
        if tool.uses_memory and (incognito or not memory_enabled):
            return Decision("deny", "memory is off for this chat")
        if tool.cross_conversation and incognito:
            return Decision("deny", "incognito chats cannot read other conversations")
        enabled, action = self.base(tool, overrides)
        if not enabled:
            return Decision("deny", f"{tool.name} is turned off in settings")
        reason = {"confirm": "side effects outside this chat" if tool.side_effect else "requires approval",
                  "deny": "blocked by policy", "allow": ""}[action]
        return Decision(action, reason)
