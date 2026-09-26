"""Access control.

The backend binds to 127.0.0.1 only. Remote (phone) access goes through `tailscale serve`,
which proxies HTTPS from your tailnet to localhost and adds `Tailscale-User-*` /
`X-Forwarded-For` headers. Rules:
  * direct loopback requests (no proxy headers) are trusted — that's this Mac;
  * proxied requests need remote access enabled *and* the bearer token;
  * the Host header must be local (or *.ts.net when remote access is on), which blocks
    DNS-rebinding attacks from web pages in your browser.
"""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Callable
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PROXY_HEADERS = ("tailscale-user-login", "x-forwarded-for", "x-forwarded-host", "forwarded")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


def load_or_create_token(path: Path) -> str:
    path = Path(path)
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    os.chmod(path, 0o600)
    return token


class AccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, remote_access: bool | Callable[[], bool], token: str | None = None,
                 token_file: Path | None = None):
        super().__init__(app)
        # A callable is read on every request, so Settings → Phone access applies immediately.
        self._remote = remote_access if callable(remote_access) else (lambda: remote_access)
        self._token = token
        self._token_file = Path(token_file) if token_file else None
        self._mtime = 0.0

    @property
    def remote_access(self) -> bool:
        return self._remote()

    @property
    def token(self) -> str:
        """Re-read the token file when it changes, so rotating it takes effect immediately."""
        if self._token_file is not None:
            mtime = self._token_file.stat().st_mtime if self._token_file.exists() else 0.0
            if mtime != self._mtime or self._token is None:
                self._token = load_or_create_token(self._token_file)
                self._mtime = self._token_file.stat().st_mtime
        return self._token or ""

    async def dispatch(self, request: Request, call_next):
        remote_access = self.remote_access
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        proxied = any(h in request.headers for h in PROXY_HEADERS)
        client = request.client.host if request.client else ""
        loopback_client = client in ("127.0.0.1", "::1", "testclient")
        if not loopback_client:
            return JSONResponse({"detail": "only loopback connections are accepted"}, status_code=403)
        if host not in LOCAL_HOSTS and not (remote_access and host.endswith(".ts.net")):
            return JSONResponse({"detail": f"host {host!r} not allowed"}, status_code=403)
        if request.method == "OPTIONS":  # CORS preflight (phone app); the real request is checked
            return await call_next(request)
        if proxied:
            if not remote_access:
                return JSONResponse({"detail": "remote access is disabled"}, status_code=403)
            # The app shell (HTML/JS/icons) holds no data and must load to show the token
            # screen; everything under /api needs the token.
            if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
                return await call_next(request)
            supplied = _bearer(request)
            if not supplied or not hmac.compare_digest(supplied, self.token):
                return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
        return await call_next(request)


def websocket_allowed(ws, remote_access: bool, token: str) -> bool:
    """Same rules as AccessMiddleware (which only sees HTTP requests), for WebSockets."""
    client = ws.client.host if ws.client else ""
    if client not in ("127.0.0.1", "::1", "testclient"):
        return False
    host = (ws.headers.get("host") or "").rsplit(":", 1)[0].lower()
    if host not in LOCAL_HOSTS and not (remote_access and host.endswith(".ts.net")):
        return False
    if any(h in ws.headers for h in PROXY_HEADERS):
        if not remote_access:
            return False
        supplied = ws.query_params.get("token") or ws.cookies.get("la_token")
        return bool(supplied) and hmac.compare_digest(supplied, token)
    return True


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # The browser sends the login cookie with every same-origin load, including <img>, <video>
    # and <iframe>, so tokens never need to appear in URLs (where they'd end up in logs).
    return request.cookies.get("la_token")
