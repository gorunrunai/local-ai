"""Settings → Updates: VERSION vs UPDATES.md on GitHub."""

from __future__ import annotations

import httpx
import pytest

from inference.config import ROOT
from orchestrator import updates

REMOTE = """# Updates
<!-- Releasing: bump VERSION ... -->
## 0.3.0 (2026-11-20)
- Photo editing.
## 0.2.1
- Fixes.
## 0.1.0 (2026-09-25)
- First release.
"""


def test_this_repo_is_ready_to_release():
    """VERSION must match the newest entry in UPDATES.md, or the update check would mislead people."""
    releases = updates.parse_updates((ROOT / "UPDATES.md").read_text())
    assert releases and releases[0].version == updates.current_version()
    assert releases[0].notes and "<!--" not in releases[0].notes


def test_newer_versions_come_with_their_notes():
    r = updates.compare("0.1.0", REMOTE)
    assert r["ok"] and r["update_available"] and r["latest"] == "0.3.0"
    assert [x["version"] for x in r["releases"]] == ["0.3.0", "0.2.1"]          # everything since yours
    assert r["releases"][0]["date"] == "2026-11-20" and "Photo editing" in r["releases"][0]["notes"]
    assert r["releases"][1]["date"] is None
    assert "install.sh | bash" in r["install_command"]


@pytest.mark.parametrize("current", ["0.3.0", "0.10.0"])
def test_up_to_date_compares_numbers_not_text(current):
    r = updates.compare(current, REMOTE)
    assert r["ok"] and not r["update_available"] and r["releases"] == []


def test_a_file_without_versions_is_an_error():
    assert not updates.compare("0.1.0", "# Updates\nnothing yet")["ok"]


_REAL_CLIENT = httpx.AsyncClient


def _client(handler):
    return lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)


async def test_check_reads_the_file(monkeypatch):
    monkeypatch.setattr(updates, "current_version", lambda: "0.2.1")
    monkeypatch.setattr(updates.httpx, "AsyncClient", _client(lambda req: httpx.Response(200, text=REMOTE)))
    r = await updates.check("https://example.test/UPDATES.md")
    assert r["update_available"] and [x["version"] for x in r["releases"]] == ["0.3.0"]


async def test_check_explains_network_problems(monkeypatch):
    def offline(req):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(updates.httpx, "AsyncClient", _client(offline))
    r = await updates.check("https://example.test/UPDATES.md")
    assert not r["ok"] and "Couldn't reach GitHub" in r["error"]
    monkeypatch.setattr(updates.httpx, "AsyncClient", _client(lambda req: httpx.Response(404)))
    assert "Couldn't find the list of updates" in (await updates.check("https://example.test/UPDATES.md"))["error"]
    monkeypatch.setattr(updates.httpx, "AsyncClient", _client(lambda req: httpx.Response(503)))
    assert "error 503" in (await updates.check("https://example.test/UPDATES.md"))["error"]
