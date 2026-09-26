"""Local SearXNG lifecycle: reuse a running instance, explain a missing install."""

from __future__ import annotations

import pytest

from orchestrator import search
from orchestrator.search import SearchUnavailable, Searxng


async def test_uses_running_instance_without_starting(monkeypatch):
    s = Searxng()

    async def healthy(url):
        return True

    monkeypatch.setattr(s, "_healthy", healthy)
    await s.ensure("http://127.0.0.1:8888")
    assert s.proc is None


async def test_missing_install_is_explained(monkeypatch, tmp_path):
    s = Searxng()

    async def down(url):
        return False

    monkeypatch.setattr(s, "_healthy", down)
    monkeypatch.setattr(search, "VENV_PYTHON", tmp_path / "missing" / "python")
    with pytest.raises(SearchUnavailable, match="make search-setup"):
        await s.ensure("http://127.0.0.1:8888")


async def test_never_starts_for_remote_url(monkeypatch, tmp_path):
    s = Searxng()

    async def down(url):
        return False

    monkeypatch.setattr(s, "_healthy", down)
    monkeypatch.setattr(search, "VENV_PYTHON", tmp_path)  # "installed"
    with pytest.raises(SearchUnavailable, match="only a local"):
        await s.ensure("http://search.example.com")
