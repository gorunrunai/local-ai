"""Settings → Clear clutter: only what the AI made and temporary files; never the user's own things."""

from __future__ import annotations

import os
import time

import pytest_asyncio

from orchestrator import cleanup
from orchestrator.storage.db import Database
from orchestrator.storage.store import Store
from tests.unit.fakes import DIMS

DAY = 86400


@pytest_asyncio.fixture(name="store")
async def _store():
    db = await Database(":memory:", DIMS).open()
    yield Store(db)
    await db.close()


async def _att(store, tmp_path, name, kind, source, cid, age_days=0, path=None, size=100):
    p = path or tmp_path / "files" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(b"x" * size)
    att = await store.add_attachment(sha256=name, filename=name, mime=None, size=size, path=str(p), kind=kind,
                                     source=source, conversation_id=cid)
    await store.db.execute("UPDATE attachments SET created_at=? WHERE id=?", (time.time() - age_days * DAY, att["id"]))
    return att, p


async def _world(store, tmp_path):
    cid = (await store.create_conversation())["id"]
    w = {"cid": cid}
    w["old_video"] = await _att(store, tmp_path, "old.mp4", "video", "tool", cid, age_days=30, size=5000)
    w["new_video"] = await _att(store, tmp_path, "new.mp4", "video", "tool", cid, age_days=1, size=3000)
    w["chart"] = await _att(store, tmp_path, "chart.png", "image", "tool", cid, age_days=30, size=200)
    w["photo"] = await _att(store, tmp_path, "photo.jpg", "image", "upload", cid, age_days=30, size=400)
    w["voice"] = await _att(store, tmp_path, "note.webm", "audio", "voice_message", cid, age_days=30)
    # Same bytes as the user's photo: the file is shared, so it must survive clearing the tool copy.
    w["copy"] = await _att(store, tmp_path, "copy.jpg", "image", "tool", cid, age_days=30, path=w["photo"][1])
    art = await store.save_artifact_version(cid, "habit-app", "react", "Habit tracker", "export default () => null", None)
    await store.db.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (time.time() - 30 * DAY, art["id"]))
    for name, age in (("sandbox/run-old", 30 * DAY), ("sandbox/run-now", 60), ("cache/media-old", 30 * DAY)):
        d = tmp_path / ("tmp" if name.startswith("sandbox") else "") / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "out.bin").write_bytes(b"y" * 1000)
        os.utime(d, (time.time() - age, time.time() - age))
    (tmp_path / "tmp" / "incognito").mkdir(parents=True, exist_ok=True)
    os.utime(tmp_path / "tmp" / "incognito", (time.time() - 30 * DAY,) * 2)
    return w


async def test_preview_counts_without_deleting(store, tmp_path):
    w = await _world(store, tmp_path)
    r = await cleanup.run(store, tmp_path, ["videos", "files", "artifacts", "temp"])
    c = r["categories"]
    assert c["videos"] == {"count": 2, "bytes": 8000}
    assert c["files"]["count"] == 2                                  # chart + the tool copy; not the user's photo
    assert c["artifacts"]["count"] == 1
    assert c["temp"]["count"] == 2                                   # old sandbox run and old cache; not recent, not incognito
    assert not r["cleared"] and w["old_video"][1].exists()


async def test_clearing_everything_keeps_the_users_own_things(store, tmp_path):
    w = await _world(store, tmp_path)
    r = await cleanup.run(store, tmp_path, ["videos", "files", "artifacts", "temp"], dry_run=False)
    assert r["cleared"]
    for key in ("old_video", "new_video", "chart"):
        assert not w[key][1].exists()
        row = await store.get_attachment(w[key][0]["id"])
        assert row and row["path"] == ""                             # row kept, so chats can say "cleared"
    assert w["photo"][1].exists() and w["voice"][1].exists()         # uploads and voice messages untouched
    assert (await store.get_attachment(w["copy"][0]["id"]))["path"] == ""   # the tool copy is cleared...
    assert w["photo"][1].exists()                                    # ...but the shared file stays for the upload
    assert not await store.db.all("SELECT * FROM artifacts")
    assert not await store.db.all("SELECT * FROM artifact_versions")
    assert not (tmp_path / "tmp" / "sandbox" / "run-old").exists()
    assert (tmp_path / "tmp" / "sandbox" / "run-now").exists()       # may be a render in progress
    assert (tmp_path / "tmp" / "incognito").exists()


async def test_date_range(store, tmp_path):
    w = await _world(store, tmp_path)
    since, until = time.time() - 2 * DAY, time.time()
    r = await cleanup.run(store, tmp_path, ["videos"], since=since, until=until, dry_run=False)
    assert r["categories"]["videos"]["count"] == 1
    assert not w["new_video"][1].exists() and w["old_video"][1].exists()
    # Clearing again finds nothing new.
    assert (await cleanup.run(store, tmp_path, ["videos"], since=since, until=until))["categories"]["videos"]["count"] == 0


async def test_your_uploads_are_a_separate_group(store, tmp_path):
    w = await _world(store, tmp_path)
    _doc, doc_path = await _att(store, tmp_path, "notes.pdf", "document", "upload", w["cid"], age_days=30)
    proj = await store.create_project("Trip")
    pfile, pfile_path = await _att(store, tmp_path, "itinerary.pdf", "document", "upload", None, age_days=30)
    await store.db.execute("INSERT INTO project_files VALUES (?, ?)", (proj["id"], pfile["id"]))

    r = await cleanup.run(store, tmp_path, ["my_photos", "my_videos", "my_files", "my_voice"])
    c = r["categories"]
    assert c["my_photos"]["count"] == 1 and c["my_voice"]["count"] == 1
    assert c["my_files"]["count"] == 1                                # notes.pdf; not the project's file
    # Clearing uploads leaves everything the AI made.
    await cleanup.run(store, tmp_path, ["my_photos", "my_files", "my_voice"], dry_run=False)
    assert (await store.get_attachment(w["photo"][0]["id"]))["path"] == ""
    assert w["photo"][1].exists()                                     # the AI's copy still uses this file
    assert not doc_path.exists() and not w["voice"][1].exists()
    assert pfile_path.exists() and (await store.get_attachment(pfile["id"]))["path"]
    assert w["old_video"][1].exists() and w["chart"][1].exists()
    # Clearing the AI's copy too: now nothing uses the shared file, so it goes.
    await cleanup.run(store, tmp_path, ["files"], dry_run=False)
    assert not w["photo"][1].exists()
