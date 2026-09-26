"""Access control, SSRF guard, code sandbox and a real stdio MCP round trip."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.auth import AccessMiddleware
from orchestrator.mcp import MCPManager
from orchestrator.policy import Policy
from orchestrator.tools.code_exec import run_sandboxed
from orchestrator.tools.web import BlockedURL, check_public_url

ROOT = Path(__file__).resolve().parents[2]


def _app(remote: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(AccessMiddleware, remote_access=remote, token="s3cret")

    @app.get("/api/x")
    def x():
        return {"ok": True}

    return TestClient(app, base_url="http://127.0.0.1")


def test_local_requests_are_trusted():
    assert _app(False).get("/api/x").status_code == 200


def test_foreign_host_header_rejected():  # DNS rebinding
    assert _app(False).get("/api/x", headers={"host": "evil.example.com"}).status_code == 403


def test_proxied_requests_need_remote_mode_and_token():
    proxied = {"host": "mac.tail1234.ts.net", "tailscale-user-login": "me@example.com"}
    assert _app(False).get("/api/x", headers=proxied).status_code == 403
    c = _app(True)
    assert c.get("/api/x", headers=proxied).status_code == 401
    assert c.get("/api/x", headers={**proxied, "authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/x", headers={**proxied, "authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/x", headers={**proxied, "cookie": "la_token=s3cret"}).status_code == 200
    # query tokens only for media/artifact GETs, never for general API calls
    assert c.get("/api/x?token=s3cret", headers=proxied).status_code == 401
    assert c.options("/api/x", headers=proxied).status_code in (200, 405)  # preflight isn't blocked


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8090/v1/models", "http://localhost:8000/api", "http://10.0.0.5/", "http://192.168.1.1",
    "http://169.254.169.254/latest/meta-data", "http://100.101.102.103/", "http://[::1]/", "file:///etc/passwd",
    "http://mac.local/", "ftp://example.com/",
])
async def test_ssrf_guard_blocks_local_targets(url):
    with pytest.raises(BlockedURL):
        await check_public_url(url)


async def test_sandbox_blocks_network_home_and_escapes(tmp_path):
    code = (
        "import socket, os\n"
        "try:\n    socket.create_connection(('1.1.1.1', 80), 2); print('NET')\nexcept Exception: print('no-net')\n"
        "try:\n    open(os.path.expanduser('~') + '/../../Users/' + os.environ.get('USER','x') + '/.zshrc').read(); print('LEAK')\n"
        "except Exception: print('no-home')\n"
        "try:\n    open('/tmp/escape.txt','w').write('x'); print('ESCAPE')\nexcept Exception: print('no-escape')\n"
        "open('ok.txt','w').write('fine'); print('wrote')\n")
    r = await run_sandboxed(code, tmp_path / "wd", timeout_s=20)
    assert r["exit_code"] == 0, r["stderr"]
    assert r["stdout"].split() == ["no-net", "no-home", "no-escape", "wrote"]
    assert [p.name for p in r["files"]] == ["ok.txt"]


async def test_sandbox_timeout(tmp_path):
    r = await run_sandboxed("while True: pass", tmp_path / "wd", timeout_s=2)
    assert r["timed_out"]


async def test_mcp_stdio_round_trip(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"demo": {
        "command": sys.executable, "args": [str(ROOT / "tests/fixtures/mcp/demo_server.py")]}}}))
    mgr = MCPManager(cfg, tmp_dir=tmp_path)
    await mgr.start()
    try:
        assert mgr.status()[0]["status"] == "ready", mgr.status()
        tools = {t.name: t for t in mgr.tools()}
        assert set(tools) == {"mcp__demo__add", "mcp__demo__write_note"}
        res = await tools["mcp__demo__add"].run({"a": 2, "b": 40}, ctx=None)
        assert res.ok and res.content.strip() == "42"
        policy = Policy({"tools": {}, "mcp": {"default_policy": "confirm", "default_policy_read_only": "allow"}})
        assert policy.decide(tools["mcp__demo__add"], {}, incognito=False, memory_enabled=True).action == "allow"
        assert policy.decide(tools["mcp__demo__write_note"], {}, incognito=False,
                             memory_enabled=True).action == "confirm"
    finally:
        await mgr.stop()


async def test_mcp_broken_server_is_isolated(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"broken": {"command": "/nonexistent/binary"}}}))
    mgr = MCPManager(cfg, tmp_dir=tmp_path)
    await mgr.start()
    assert mgr.status()[0]["status"] == "error" and mgr.tools() == []
    await mgr.stop()


def test_artifact_cdn_scripts_point_to_local_copies():
    from orchestrator.api import localize_cdn_scripts

    page = ('<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>'
            "<script src='https://d3js.org/d3.v7.min.js'></script>"
            '<script src="https://cdn.tailwindcss.com"></script>'
            '<script src="https://cdn.jsdelivr.net/npm/d3-scale@4"></script>'
            '<script src="https://evil.example/steal.js"></script>')
    out = localize_cdn_scripts(page)
    assert '"/artifact-runtime/chart.js"' in out and "'/artifact-runtime/d3.js'" in out
    assert '"/artifact-runtime/tailwind.js"' in out
    # Anything else keeps its URL, which the sandbox's CSP then blocks.
    assert "d3-scale@4" in out and "https://evil.example/steal.js" in out
