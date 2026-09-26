"""Phone-access switch: Tailscale checks, `tailscale serve`, persistence, live middleware."""

from __future__ import annotations

import json
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator import remote as remote_mod
from orchestrator.auth import AccessMiddleware
from orchestrator.remote import RemoteAccess, RemoteError


def fake_tailscale(tmp_path, state="Running", serve_ok=True):
    """A stand-in `tailscale` that answers status/serve and logs its arguments."""
    log = tmp_path / "calls.log"
    script = tmp_path / "tailscale"
    script.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$1 $2" in
  "status --json") echo '{{"BackendState": "{state}", "Self": {{"DNSName": "my-mac.tail123.ts.net.", "OS": "macOS"}}, "Peer": {{"a": {{"HostName": "my-iphone", "OS": "iOS", "Online": true}}, "b": {{"HostName": "nas", "OS": "linux", "Online": true}}}}}}' ;;
  "serve status") if [ -f {tmp_path}/serving ]; then echo '{{"Web": {{"x": {{"Handlers": {{"/": {{"Proxy": "http://127.0.0.1:8000"}}}}}}}}}}'; else echo '{{}}'; fi ;;
  "serve --bg") {"touch " + str(tmp_path / "serving") if serve_ok else "echo 'Serve is not enabled on your tailnet. To enable, visit: https://login.tailscale.com/f/serve'; exit 1"} ;;
  "serve --https=443") rm -f {tmp_path}/serving ;;
esac
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


async def test_turn_on_and_off(tmp_path, monkeypatch):
    binary, log = fake_tailscale(tmp_path)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    r = RemoteAccess(tmp_path / "data", 8000)
    st = await r.set_enabled(True)
    assert r.enabled and st["serving"] and st["url"] == "https://my-mac.tail123.ts.net"
    assert "serve --bg 8000" in log.read_text()
    assert RemoteAccess(tmp_path / "data", 8000).enabled          # saved across restarts
    st = await r.set_enabled(False)
    assert not r.enabled and not st["serving"]
    assert json.loads((tmp_path / "data" / "remote_access.json").read_text()) == {"enabled": False, "paused_by_web": False}


async def test_explains_why_it_cant_turn_on(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: None)
    r = RemoteAccess(tmp_path, 8000)
    with pytest.raises(RemoteError, match="Install Tailscale"):
        await r.set_enabled(True)
    await r.set_enabled(False)                     # turning off always works
    binary, _ = fake_tailscale(tmp_path, state="NeedsLogin")
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    with pytest.raises(RemoteError, match="sign in"):
        await r.set_enabled(True)
    binary, _ = fake_tailscale(tmp_path, serve_ok=False)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    with pytest.raises(RemoteError, match="login.tailscale.com"):  # Tailscale's own fix-it link
        await r.set_enabled(True)
    assert not r.enabled


def test_env_default_until_first_toggle(tmp_path):
    assert RemoteAccess(tmp_path, 8000, default=True).enabled
    (tmp_path / "remote_access.json").write_text('{"enabled": false}')
    assert not RemoteAccess(tmp_path, 8000, default=True).enabled


def test_middleware_follows_the_switch_without_restart():
    state = {"on": False}
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    app.add_middleware(AccessMiddleware, remote_access=lambda: state["on"], token="s3cret")
    c = TestClient(app)
    proxied = {"Host": "my-mac.tail123.ts.net", "X-Forwarded-For": "100.64.0.9",
               "Authorization": "Bearer s3cret"}
    assert c.get("/api/ping", headers=proxied).status_code == 403
    state["on"] = True
    assert c.get("/api/ping", headers=proxied).status_code == 200


async def test_status_lists_phones_on_the_tailnet(tmp_path, monkeypatch):
    """Setup step 2 ticks itself once a phone shows up on the same Tailscale network."""
    binary, _ = fake_tailscale(tmp_path)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    st = await RemoteAccess(tmp_path / "data", 8000).status()
    assert st["phones"] == [{"name": "my-iphone", "os": "iOS", "online": True}]     # the NAS isn't a phone


async def test_phone_access_follows_web_access_only_when_web_access_turned_it_off(tmp_path, monkeypatch):
    binary, _ = fake_tailscale(tmp_path)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    r = RemoteAccess(tmp_path / "data", 8000)

    # Phone access on; web access goes off -> phone access goes off, remembered.
    await r.set_by_user(True)
    assert await r.pause_for_web_off() and not r.enabled and r.paused_by_web
    assert RemoteAccess(tmp_path / "data", 8000).paused_by_web              # survives a restart
    # Web access back on -> phone access comes back.
    assert await r.resume_for_web_on() is None
    assert r.enabled and not r.paused_by_web

    # The user turned phone access off themselves; web access off, then on -> it stays off.
    await r.set_by_user(False)
    assert not await r.pause_for_web_off() and not r.paused_by_web
    await r.resume_for_web_on()
    assert not r.enabled


async def test_the_users_own_choice_replaces_an_automatic_pause(tmp_path, monkeypatch):
    binary, _ = fake_tailscale(tmp_path)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    r = RemoteAccess(tmp_path / "data", 8000)
    await r.set_by_user(True)
    await r.pause_for_web_off()
    await r.set_by_user(False)                      # user says "off" while web access is off
    await r.resume_for_web_on()
    assert not r.enabled


async def test_restoring_explains_when_tailscale_is_not_running(tmp_path, monkeypatch):
    binary, _ = fake_tailscale(tmp_path)
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: binary)
    r = RemoteAccess(tmp_path / "data", 8000)
    await r.set_by_user(True)
    await r.pause_for_web_off()
    (tmp_path / "stopped").mkdir()
    stopped, _ = fake_tailscale(tmp_path / "stopped", state="Stopped")
    monkeypatch.setattr(remote_mod, "tailscale_binary", lambda: stopped)
    msg = await r.resume_for_web_on()
    assert msg and "couldn't turn back on" in msg and not r.enabled
