"""MCP client: connect to stdio / Streamable-HTTP servers from config/mcp.json and expose their tools.

Config format (same shape as other MCP clients):
    {"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Docs"]},
        "remote": {"url": "http://127.0.0.1:9000/mcp", "policy": "allow"}}}
Optional per-server keys: "enabled" (default true), "policy" (allow | confirm | deny; default:
allow for tools the server marks read-only, confirm for everything else), "env", "cwd".
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from inference.config import ROOT
from orchestrator.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)
CONFIG_PATH = ROOT / "config" / "mcp.json"


@dataclass
class ServerState:
    name: str
    config: dict
    status: str = "stopped"      # stopped | connecting | ready | error
    error: str | None = None
    tools: list = field(default_factory=list)
    client: object | None = None
    task: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:48]


class MCPTool(Tool):
    def __init__(self, manager: MCPManager, server: str, tool):
        self.manager = manager
        self.server = server
        self.remote_name = tool.name
        self.name = f"mcp__{_safe(server)}__{_safe(tool.name)}"
        self.description = f"[MCP server '{server}'] {tool.description or tool.name}"
        self.parameters = tool.input_schema or {"type": "object", "properties": {}}
        ann = tool.annotations
        read_only = bool(ann and getattr(ann, "read_only_hint", False))
        self.side_effect = not read_only
        self.mcp_policy = manager.states[server].config.get("policy")

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        return await self.manager.call(self.server, self.remote_name, args, ctx)


class MCPManager:
    def __init__(self, config_path: Path = CONFIG_PATH, tmp_dir: Path | None = None):
        self.config_path = config_path
        self.tmp_dir = tmp_dir or ROOT / "data" / "tmp" / "mcp"
        self.states: dict[str, ServerState] = {}

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text()).get("mcpServers", {})

    async def start(self) -> None:
        for name, conf in self.load_config().items():
            if conf.get("enabled", True):
                await self.start_server(name, conf)

    async def start_server(self, name: str, conf: dict, wait_s: float = 20) -> ServerState:
        st = ServerState(name, conf, status="connecting")
        self.states[name] = st
        ready = asyncio.Event()
        st.task = asyncio.create_task(self._serve(st, ready), name=f"mcp:{name}")
        try:
            await asyncio.wait_for(ready.wait(), wait_s)
        except TimeoutError:
            st.status, st.error = "error", f"no response within {wait_s:.0f}s"
        return st

    async def _serve(self, st: ServerState, ready: asyncio.Event) -> None:
        """Owns the client context for its whole life (anyio cancel scopes must exit in-task)."""
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        conf = st.config
        try:
            if "url" in conf:
                target = conf["url"]
            else:
                target = StdioServerParameters(command=conf["command"], args=conf.get("args", []),
                                               env=conf.get("env"), cwd=conf.get("cwd"))
            async with Client(target, read_timeout_seconds=conf.get("timeout_s", 60)) as client:
                st.client = client
                st.tools = list((await client.list_tools()).tools)
                st.status, st.error = "ready", None
                ready.set()
                await st.stop.wait()
        except Exception as e:  # noqa: BLE001 - a broken server must not take the app down
            log.warning("MCP server %s failed: %s", st.name, e)
            st.status, st.error = "error", str(e)[:500]
        finally:
            st.client = None
            if st.status == "ready":
                st.status = "stopped"
            ready.set()

    async def stop(self) -> None:
        for st in self.states.values():
            st.stop.set()
        tasks = [st.task for st in self.states.values() if st.task]
        if tasks:
            await asyncio.wait(tasks, timeout=10)

    async def reload(self) -> None:
        await self.stop()
        self.states.clear()
        await self.start()

    def tools(self) -> list[MCPTool]:
        return [MCPTool(self, name, t) for name, st in self.states.items() if st.status == "ready"
                for t in st.tools]

    def status(self) -> list[dict]:
        return [{"name": st.name, "status": st.status, "error": st.error,
                 "transport": "http" if "url" in st.config else "stdio",
                 "tools": [t.name for t in st.tools], "policy": st.config.get("policy")}
                for st in self.states.values()]

    async def call(self, server: str, tool: str, args: dict, ctx: ToolContext) -> ToolResult:
        st = self.states.get(server)
        if not st or st.client is None:
            return ToolResult(f"MCP server '{server}' is not connected.", ok=False)
        res = await st.client.call_tool(tool, args)
        texts, images = [], []
        for block in res.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                texts.append(block.text)
            elif kind == "image":
                self.tmp_dir.mkdir(parents=True, exist_ok=True)
                ext = (block.mime_type or "image/png").split("/")[-1]
                p = self.tmp_dir / f"{server}-{tool}-{len(images)}.{ext}"
                p.write_bytes(base64.b64decode(block.data))
                images.append(str(p))
            else:
                texts.append(f"[{kind} content]")
        if res.structured_content is not None and not texts:
            texts.append(json.dumps(res.structured_content, ensure_ascii=False)[:20_000])
        return ToolResult("\n".join(texts) or "(no output)", ok=not res.is_error, images=images,
                          data={"server": server, "tool": tool})
