"""Phone access over Tailscale, switched on and off from Settings → Phone access.

Turning it on runs `tailscale serve --bg <port>`: Tailscale answers HTTPS on
https://<mac>.<tailnet>.ts.net and forwards to the backend on 127.0.0.1. The backend itself
never listens beyond loopback; AccessMiddleware reads `enabled` on every request, so the
switch takes effect without a restart. The choice is saved in data/remote_access.json
(the REMOTE_ACCESS env/.env setting is only the default before it's first toggled).

Phone access follows the Web access switch: turning web access off turns phone access off too and
remembers that (`paused_by_web`); turning web access back on restores it only in that case. If the
user had turned phone access off themselves, it stays off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)
APP_BINARY = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


class RemoteError(RuntimeError):
    """Shown to the user as-is."""


def tailscale_binary() -> str | None:
    # The Mac App Store / standalone app doesn't put `tailscale` on PATH; its binary is the CLI.
    if found := shutil.which("tailscale"):
        return found
    return str(APP_BINARY) if APP_BINARY.exists() else None


class RemoteAccess:
    def __init__(self, data_dir: Path, port: int, default: bool = False):
        self.state_file = Path(data_dir) / "remote_access.json"
        self.port = port
        self.enabled = default
        self.paused_by_web = False
        try:
            saved = json.loads(self.state_file.read_text())
            self.enabled = bool(saved["enabled"])
            self.paused_by_web = bool(saved.get("paused_by_web", False))
        except (OSError, ValueError, KeyError):
            pass
        self._lock = asyncio.Lock()

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"enabled": self.enabled, "paused_by_web": self.paused_by_web}))

    async def _tailscale(self, *args: str, timeout: float = 20) -> tuple[int, str]:
        binary = tailscale_binary()
        if not binary:
            raise RemoteError("Tailscale isn't installed on this Mac.")
        proc = await asyncio.create_subprocess_exec(
            binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            out, _ = await proc.communicate()
            return -1, out.decode(errors="replace")
        return proc.returncode or 0, out.decode(errors="replace")

    async def status(self) -> dict:
        """Tailscale state for the settings screen; never raises."""
        info = {"installed": tailscale_binary() is not None, "running": False, "dns_name": None,
                "serving": False, "url": None, "phones": []}
        if not info["installed"]:
            return info
        try:
            code, out = await self._tailscale("status", "--json", timeout=5)
            data = json.loads(out) if code == 0 else {}
            info["running"] = data.get("BackendState") == "Running"
            name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
            if name:
                info["dns_name"], info["url"] = name, f"https://{name}"
            # Phones on the same tailnet, so setup can tell when step 2 (Tailscale on the phone) is done.
            info["phones"] = [{"name": p.get("HostName") or "phone", "os": p.get("OS"), "online": bool(p.get("Online"))}
                              for p in (data.get("Peer") or {}).values() if p.get("OS") in ("iOS", "android")]
            code, out = await self._tailscale("serve", "status", "--json", timeout=5)
            info["serving"] = code == 0 and f"127.0.0.1:{self.port}" in out
        except (RemoteError, ValueError, OSError) as e:
            log.debug("tailscale status failed: %s", e)
        return info

    async def set_enabled(self, on: bool) -> dict:
        async with self._lock:
            if on:
                st = await self.status()
                if not st["installed"]:
                    raise RemoteError("Install Tailscale on this Mac first.")
                if not st["running"]:
                    raise RemoteError("Open Tailscale on this Mac and sign in, then try again.")
                code, out = await self._tailscale("serve", "--bg", str(self.port), timeout=30)
                if code != 0:
                    # e.g. HTTPS or Serve not yet enabled for the tailnet; Tailscale's message
                    # includes the admin-console link that fixes it.
                    raise RemoteError(out.strip() or "tailscale serve failed.")
            else:
                try:
                    await self._tailscale("serve", "--https=443", "off", timeout=15)
                except RemoteError:
                    pass  # Tailscale gone: nothing is being forwarded anyway
            self.enabled = on
            self._save()
        return await self.status()

    async def pause_for_web_off(self) -> bool:
        """Web access was turned off: turn phone access off too, remembering that we did."""
        if not self.enabled:
            return False                        # already off by the user's choice: nothing to restore later
        await self.set_enabled(False)
        self.paused_by_web = True
        self._save()
        return True

    async def resume_for_web_on(self) -> str | None:
        """Web access was turned back on: restore phone access only if web access turned it off.
        Returns a message if it couldn't come back (e.g. Tailscale isn't running now)."""
        if not self.paused_by_web:
            return None
        self.paused_by_web = False
        self._save()
        try:
            await self.set_enabled(True)
        except RemoteError as e:
            return f"Phone access couldn't turn back on: {e}"
        return None

    async def set_by_user(self, on: bool) -> dict:
        """The Phone access switch itself: the user's own choice replaces any automatic pause."""
        self.paused_by_web = False
        return await self.set_enabled(on)
