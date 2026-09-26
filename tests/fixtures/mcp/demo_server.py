"""Tiny MCP server used by the tests (stdio transport)."""

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("demo")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


@server.tool()
def write_note(text: str) -> str:
    """Pretend to write a note somewhere (has side effects)."""
    return f"wrote: {text}"


if __name__ == "__main__":
    server.run()
