"""FastAPI application: `uv run uvicorn orchestrator.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers`.

Keep --no-proxy-headers: AccessMiddleware needs the real peer address (see orchestrator/auth.py).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from inference.config import ROOT
from orchestrator.api import router
from orchestrator.auth import AccessMiddleware, load_or_create_token
from orchestrator.chat import AppState, ChatService
from orchestrator.remote import RemoteAccess
from orchestrator.search import searxng
from orchestrator.settings import AppSettings, ensure_dirs, get_settings

log = logging.getLogger(__name__)


def create_app(settings: AppSettings | None = None, *, start_models: bool = True, app_state=None) -> FastAPI:
    settings = settings or get_settings()
    ensure_dirs(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.app_state = app_state or await AppState.create(settings, start_models=start_models)
        s = app.state.app_state
        if start_models:
            # Load the default model and prime its prompt cache without delaying startup.
            s.spawn(ChatService(s).warm_up())
        log.info("ready on http://%s:%s", settings.app_host, settings.app_port)
        yield
        await searxng.stop()
        await s.close()

    app = FastAPI(title="GoRunRun Local AI", version="0.2.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    load_or_create_token(settings.auth_token_file)
    remote = RemoteAccess(settings.data_dir, settings.app_port, default=settings.remote_access)
    app.state.remote = remote
    app.add_middleware(AccessMiddleware, remote_access=lambda: remote.enabled,
                       token_file=settings.auth_token_file)
    app.include_router(router)
    _serve_frontend(app)
    return app


def _serve_frontend(app: FastAPI) -> None:
    """Serve the built PWA (frontend/dist) on the same origin as the API, with SPA fallback."""
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f, headers=no_cache if f.name in ("sw.js", "index.html") else None)
        return FileResponse(dist / "index.html", headers=no_cache)


app = create_app()
