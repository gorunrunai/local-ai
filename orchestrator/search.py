"""Local SearXNG for web_search: installed by `make search-setup`, started on first search.

SearXNG runs from its own venv (`searxng/.venv`) on 127.0.0.1 with config/searxng.yml. The
backend starts it the first time the model searches (a few seconds) and stops it on shutdown.
If SearXNG is already running on that address (e.g. a Docker instance), it is used as is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import signal

import httpx

from inference.config import ROOT

log = logging.getLogger(__name__)
VENV_PYTHON = ROOT / "searxng" / ".venv" / "bin" / "python"
SETTINGS = ROOT / "config" / "searxng.yml"


class SearchUnavailable(RuntimeError):
    pass


class Searxng:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._log = None

    @staticmethod
    def installed() -> bool:
        return VENV_PYTHON.exists()

    async def _healthy(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                return (await c.get(f"{url}/healthz")).status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure(self, url: str) -> None:
        """Make sure SearXNG answers at `url`, starting the local install if needed."""
        if await self._healthy(url):
            return
        async with self._lock:
            if await self._healthy(url):
                return
            if not self.installed():
                raise SearchUnavailable("SearXNG isn't installed; run `make search-setup`")
            if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
                raise SearchUnavailable(f"{url} isn't reachable, and only a local SearXNG can be started")
            data = ROOT / "data"
            data.mkdir(exist_ok=True)
            secret_file = data / "searxng_secret"
            if not secret_file.exists():
                secret_file.write_text(secrets.token_hex(32))
                secret_file.chmod(0o600)
            (data / "logs").mkdir(exist_ok=True)
            self._log = (data / "logs" / "searxng.log").open("ab")
            # PYTHONPATH, not an editable install: macOS can flag venv .pth files as hidden, and
            # Python 3.12.14+ skips hidden .pth files, so an editable install stops importing.
            env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", ""), "LANG": "en_US.UTF-8",
                   "PYTHONPATH": str(ROOT / "searxng" / "src"),
                   "SEARXNG_SETTINGS_PATH": str(SETTINGS), "SEARXNG_SECRET": secret_file.read_text().strip()}
            self.proc = await asyncio.create_subprocess_exec(
                str(VENV_PYTHON), "-m", "searx.webapp", cwd=str(ROOT / "searxng"), env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=self._log, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True)
            for _ in range(60):
                if await self._healthy(url):
                    log.info("SearXNG started at %s", url)
                    return
                if self.proc.returncode is not None:
                    break
                await asyncio.sleep(0.5)
            await self.stop()
            raise SearchUnavailable("SearXNG failed to start; see data/logs/searxng.log")

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None
        if self._log:
            self._log.close()
            self._log = None


searxng = Searxng()
