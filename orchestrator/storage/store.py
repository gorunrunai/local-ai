"""Repository: every read/write the orchestrator makes, in one place."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from orchestrator.storage.db import Database, dumps, new_id, now, vec_blob

JSON_FIELDS = {"blocks", "citations", "usage", "meta", "value", "payload"}


def _decode(row: dict | None) -> dict | None:
    if row is None:
        return None
    for k in JSON_FIELDS & row.keys():
        if isinstance(row[k], str):
            try:
                row[k] = json.loads(row[k])
            except json.JSONDecodeError:
                pass
    return row


class Store:
    def __init__(self, db: Database, incognito: bool = False):
        self.db = db
        self.incognito = incognito

    # --- projects -----------------------------------------------------------------------
    async def create_project(self, name: str, instructions: str = "") -> dict:
        pid, t = new_id("prj_"), now()
        await self.db.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (pid, name, instructions, t, t))
        return await self.get_project(pid)

    async def get_project(self, pid: str) -> dict | None:
        return await self.db.one("SELECT * FROM projects WHERE id=?", (pid,))

    async def list_projects(self) -> list[dict]:
        return await self.db.all("SELECT * FROM projects ORDER BY updated_at DESC")

    async def update_project(self, pid: str, **fields: Any) -> dict | None:
        await self._update("projects", pid, fields, {"name", "instructions"})
        return await self.get_project(pid)

    async def delete_project(self, pid: str) -> None:
        await self.db.execute("DELETE FROM chunks WHERE project_id=? AND source_type='project_file'", (pid,))
        await self.db.execute("DELETE FROM projects WHERE id=?", (pid,))

    async def add_project_file(self, pid: str, attachment_id: str) -> None:
        await self.db.execute("INSERT OR IGNORE INTO project_files VALUES (?,?)", (pid, attachment_id))

    async def remove_project_file(self, pid: str, attachment_id: str) -> None:
        await self.db.execute("DELETE FROM project_files WHERE project_id=? AND attachment_id=?",
                              (pid, attachment_id))
        await self.delete_chunks("project_file", attachment_id)

    async def project_files(self, pid: str) -> list[dict]:
        return await self.db.all(
            "SELECT a.* FROM attachments a JOIN project_files pf ON pf.attachment_id=a.id "
            "WHERE pf.project_id=? ORDER BY a.created_at", (pid,))

    # --- conversations ------------------------------------------------------------------
    async def create_conversation(self, project_id: str | None = None, model: str | None = None,
                                  style: str | None = None, title: str | None = None) -> dict:
        cid, t = new_id("inc_" if self.incognito else "conv_"), now()
        await self.db.execute(
            "INSERT INTO conversations (id,title,project_id,model,style,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)", (cid, title, project_id, model, style, t, t))
        return await self.get_conversation(cid)

    async def get_conversation(self, cid: str) -> dict | None:
        row = await self.db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if row:
            row["incognito"] = self.incognito
        return row

    async def list_conversations(self, project_id: str | None = None, starred: bool | None = None,
                                 query: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        where, params = [], []
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if starred is not None:
            where.append("starred=?")
            params.append(int(starred))
        if query:
            where.append("title LIKE ?")
            params.append(f"%{query}%")
        sql = "SELECT * FROM conversations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY starred DESC, updated_at DESC LIMIT ? OFFSET ?"
        return await self.db.all(sql, (*params, limit, offset))

    async def update_conversation(self, cid: str, **fields: Any) -> dict | None:
        await self._update("conversations", cid, fields,
                           {"title", "project_id", "starred", "model", "style", "active_leaf_id"})
        return await self.get_conversation(cid)

    async def touch_conversation(self, cid: str) -> None:
        await self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid))

    async def delete_conversation(self, cid: str) -> None:
        await self.db.execute("DELETE FROM prompt_debug WHERE message_id IN "
                              "(SELECT id FROM messages WHERE conversation_id=?)", (cid,))
        ids = [r["id"] for r in await self.db.all("SELECT id FROM chunks WHERE conversation_id=?", (cid,))]
        await self._delete_chunk_ids(ids)
        await self.db.execute("DELETE FROM conversations WHERE id=?", (cid,))

    # --- messages -----------------------------------------------------------------------
    async def add_message(self, cid: str, parent_id: str | None, role: str, content: str = "",
                          attachment_ids: list[str] | None = None, model: str | None = None,
                          status: str = "complete") -> dict:
        mid = new_id("msg_")
        await self.db.execute(
            "INSERT INTO messages (id,conversation_id,parent_id,role,content,model,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (mid, cid, parent_id, role, content, model, status, now()))
        for i, aid in enumerate(attachment_ids or []):
            await self.db.execute("INSERT OR IGNORE INTO message_attachments VALUES (?,?,?)", (mid, aid, i))
        await self.db.execute("UPDATE conversations SET active_leaf_id=?, updated_at=? WHERE id=?",
                              (mid, now(), cid))
        return await self.get_message(mid)

    async def get_message(self, mid: str) -> dict | None:
        row = _decode(await self.db.one("SELECT * FROM messages WHERE id=?", (mid,)))
        if row:
            row["attachment_ids"] = [r["attachment_id"] for r in await self.db.all(
                "SELECT attachment_id FROM message_attachments WHERE message_id=? ORDER BY ord", (mid,))]
        return row

    async def update_message(self, mid: str, **fields: Any) -> None:
        for k in ("blocks", "citations", "usage"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = dumps(fields[k])
        await self._update("messages", mid, fields,
                           {"content", "blocks", "citations", "model", "status", "pinned", "usage"},
                           touch=False)

    async def path_to(self, mid: str) -> list[dict]:
        """Messages from the root down to `mid` (inclusive)."""
        rows = await self.db.all(
            "WITH RECURSIVE p(id, parent_id, depth) AS ("
            " SELECT id, parent_id, 0 FROM messages WHERE id=?"
            " UNION ALL SELECT m.id, m.parent_id, p.depth+1 FROM messages m JOIN p ON m.id=p.parent_id)"
            " SELECT id FROM p ORDER BY depth DESC", (mid,))
        return [await self.get_message(r["id"]) for r in rows]

    async def active_path(self, cid: str) -> list[dict]:
        conv = await self.get_conversation(cid)
        if not conv or not conv["active_leaf_id"]:
            return []
        return await self.path_to(conv["active_leaf_id"])

    async def children(self, cid: str, parent_id: str | None) -> list[dict]:
        if parent_id is None:
            return await self.db.all("SELECT id, created_at FROM messages WHERE conversation_id=? "
                                     "AND parent_id IS NULL ORDER BY created_at", (cid,))
        return await self.db.all("SELECT id, created_at FROM messages WHERE parent_id=? "
                                 "ORDER BY created_at", (parent_id,))

    async def latest_leaf(self, mid: str) -> str:
        """Follow the most recent child repeatedly: the leaf shown when switching to `mid`."""
        cur = mid
        while True:
            row = await self.db.one("SELECT id FROM messages WHERE parent_id=? "
                                    "ORDER BY created_at DESC LIMIT 1", (cur,))
            if not row:
                return cur
            cur = row["id"]

    async def all_messages(self, cid: str) -> list[dict]:
        rows = await self.db.all("SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at",
                                 (cid,))
        return [await self.get_message(r["id"]) for r in rows]

    # --- attachments --------------------------------------------------------------------
    async def add_attachment(self, *, sha256: str, filename: str, mime: str | None, size: int,
                             path: str, kind: str | None, source: str,
                             conversation_id: str | None) -> dict:
        aid = new_id("att_")
        await self.db.execute(
            "INSERT INTO attachments (id,sha256,filename,mime,size,path,kind,source,conversation_id,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, sha256, filename, mime, size, path, kind, source, conversation_id, now()))
        return await self.get_attachment(aid)

    async def get_attachment(self, aid: str) -> dict | None:
        return await self.db.one("SELECT * FROM attachments WHERE id=?", (aid,))

    async def attachments(self, ids: list[str]) -> list[dict]:
        out = []
        for aid in ids:
            if row := await self.get_attachment(aid):
                out.append(row)
        return out

    async def conversation_attachments(self, cid: str) -> list[dict]:
        return await self.db.all("SELECT * FROM attachments WHERE conversation_id=? ORDER BY created_at",
                                 (cid,))

    async def update_attachment(self, aid: str, **fields: Any) -> None:
        await self._update("attachments", aid, fields, {"kind", "voice_note", "conversation_id"},
                           touch=False)

    # --- tool events --------------------------------------------------------------------
    async def add_tool_event(self, *, message_id: str, call_id: str, name: str, args: dict,
                             result: Any, status: str, decision: str, started_at: float,
                             duration_ms: int) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO tool_events VALUES (?,?,?,?,?,?,?,?,?)",
            (call_id, message_id, name, dumps(args), dumps(result), status, decision, started_at,
             duration_ms))

    async def tool_events(self, message_id: str) -> list[dict]:
        return await self.db.all("SELECT * FROM tool_events WHERE message_id=? ORDER BY started_at",
                                 (message_id,))

    # --- memories -----------------------------------------------------------------------
    async def add_memory(self, text: str, embedding: np.ndarray, source_conversation_id: str | None) -> dict:
        mid, t = new_id("mem_"), now()
        cur = await self.db.execute("INSERT INTO memories VALUES (?,?,?,?,?)",
                                    (mid, text, source_conversation_id, t, t))
        await self.db.execute("INSERT INTO memories_vec(rowid, embedding) VALUES (?,?)",
                              (cur.lastrowid, vec_blob(embedding)))
        return await self.db.one("SELECT * FROM memories WHERE id=?", (mid,))

    async def update_memory(self, mid: str, text: str, embedding: np.ndarray) -> dict | None:
        row = await self.db.one("SELECT rowid FROM memories WHERE id=?", (mid,))
        if not row:
            return None
        await self.db.execute("UPDATE memories SET text=?, updated_at=? WHERE id=?", (text, now(), mid))
        await self.db.execute("DELETE FROM memories_vec WHERE rowid=?", (row["rowid"],))
        await self.db.execute("INSERT INTO memories_vec(rowid, embedding) VALUES (?,?)",
                              (row["rowid"], vec_blob(embedding)))
        return await self.db.one("SELECT * FROM memories WHERE id=?", (mid,))

    async def delete_memory(self, mid: str) -> bool:
        row = await self.db.one("SELECT rowid FROM memories WHERE id=?", (mid,))
        if not row:
            return False
        await self.db.execute("DELETE FROM memories_vec WHERE rowid=?", (row["rowid"],))
        await self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
        return True

    async def list_memories(self) -> list[dict]:
        return await self.db.all("SELECT * FROM memories ORDER BY updated_at DESC")

    async def search_memories(self, embedding: np.ndarray, k: int = 8) -> list[dict]:
        return await self.db.all(
            "SELECT m.*, v.distance FROM (SELECT rowid, distance FROM memories_vec "
            "WHERE embedding MATCH ? AND k = ?) v JOIN memories m ON m.rowid = v.rowid "
            "ORDER BY v.distance", (vec_blob(embedding), k))

    # --- retrieval chunks ---------------------------------------------------------------
    async def add_chunks(self, rows: list[dict], embeddings: np.ndarray) -> None:
        for row, emb in zip(rows, embeddings, strict=True):
            cur = await self.db.conn.execute(
                "INSERT INTO chunks (source_type,source_id,project_id,conversation_id,ord,text,meta) "
                "VALUES (?,?,?,?,?,?,?)",
                (row["source_type"], row["source_id"], row.get("project_id"),
                 row.get("conversation_id"), row.get("ord", 0), row["text"], dumps(row.get("meta", {}))))
            await self.db.conn.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES (?,?)",
                                       (cur.lastrowid, vec_blob(emb)))
        await self.db.conn.commit()

    async def has_chunks(self, source_type: str, source_id: str) -> bool:
        return bool(await self.db.scalar("SELECT 1 FROM chunks WHERE source_type=? AND source_id=? "
                                         "LIMIT 1", (source_type, source_id)))

    async def delete_chunks(self, source_type: str, source_id: str) -> None:
        ids = [r["id"] for r in await self.db.all(
            "SELECT id FROM chunks WHERE source_type=? AND source_id=?", (source_type, source_id))]
        await self._delete_chunk_ids(ids)

    async def _delete_chunk_ids(self, ids: list[int]) -> None:
        for i in ids:
            await self.db.conn.execute("DELETE FROM chunks_vec WHERE rowid=?", (i,))
            await self.db.conn.execute("DELETE FROM chunks WHERE id=?", (i,))
        await self.db.conn.commit()

    @staticmethod
    def _chunk_filter(scope: dict) -> tuple[str, list]:
        clauses, params = [], []
        for key in ("source_type", "project_id", "conversation_id"):
            val = scope.get(key)
            if val is None:
                continue
            if isinstance(val, (list, tuple)):
                clauses.append(f"c.{key} IN ({','.join('?' * len(val))})")
                params.extend(val)
            else:
                clauses.append(f"c.{key}=?")
                params.append(val)
        if ex := scope.get("exclude_conversation_id"):
            clauses.append("(c.conversation_id IS NULL OR c.conversation_id != ?)")
            params.append(ex)
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    async def search_chunks_vec(self, embedding: np.ndarray, scope: dict, k: int = 20) -> list[dict]:
        where, params = self._chunk_filter(scope)
        # Over-fetch from the ANN index, then filter by scope.
        rows = await self.db.all(
            "SELECT c.*, v.distance FROM (SELECT rowid, distance FROM chunks_vec "
            "WHERE embedding MATCH ? AND k = ?) v JOIN chunks c ON c.id = v.rowid "
            f"WHERE 1=1{where} ORDER BY v.distance LIMIT ?", (vec_blob(embedding), max(200, k * 10),
                                                               *params, k))
        return [_decode(r) for r in rows]

    async def search_chunks_fts(self, query: str, scope: dict, k: int = 20) -> list[dict]:
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms[:16])
        where, params = self._chunk_filter(scope)
        rows = await self.db.all(
            "SELECT c.*, bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ?{where} ORDER BY score LIMIT ?", (match, *params, k))
        return [_decode(r) for r in rows]

    # --- artifacts ----------------------------------------------------------------------
    async def save_artifact_version(self, cid: str, identifier: str, type_: str, title: str,
                                    content: str, message_id: str | None) -> dict:
        art = await self.db.one("SELECT * FROM artifacts WHERE conversation_id=? AND identifier=?",
                                (cid, identifier))
        t = now()
        if not art:
            aid = new_id("art_")
            await self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
                                  (aid, cid, identifier, type_, title, t, t))
            version = 1
        else:
            aid = art["id"]
            version = await self.db.scalar("SELECT MAX(version) FROM artifact_versions WHERE artifact_id=?",
                                           (aid,)) + 1
            await self.db.execute("UPDATE artifacts SET type=?, title=?, updated_at=? WHERE id=?",
                                  (type_, title, t, aid))
        await self.db.execute("INSERT INTO artifact_versions VALUES (?,?,?,?,?)",
                              (aid, version, content, message_id, t))
        return {"id": aid, "identifier": identifier, "type": type_, "title": title, "version": version}

    async def get_artifact(self, aid: str) -> dict | None:
        art = await self.db.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        if art:
            art["versions"] = await self.db.all(
                "SELECT version, content, message_id, created_at FROM artifact_versions "
                "WHERE artifact_id=? ORDER BY version", (aid,))
        return art

    async def find_artifact(self, cid: str, identifier: str) -> dict | None:
        art = await self.db.one("SELECT id FROM artifacts WHERE conversation_id=? AND identifier=?",
                                (cid, identifier))
        return await self.get_artifact(art["id"]) if art else None

    async def list_artifacts(self, cid: str) -> list[dict]:
        return await self.db.all(
            "SELECT a.*, (SELECT MAX(version) FROM artifact_versions v WHERE v.artifact_id=a.id) AS latest "
            "FROM artifacts a WHERE conversation_id=? ORDER BY created_at", (cid,))

    # --- summaries / debug / settings ---------------------------------------------------
    async def summaries_for(self, cid: str) -> list[dict]:
        return await self.db.all("SELECT * FROM summaries WHERE conversation_id=? ORDER BY created_at DESC",
                                 (cid,))

    async def save_summary(self, cid: str, upto: str, text: str, tokens: int) -> None:
        await self.db.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?)",
                              (cid, upto, text, tokens, now()))

    async def save_prompt_debug(self, mid: str, payload: dict) -> None:
        await self.db.execute("INSERT OR REPLACE INTO prompt_debug VALUES (?,?,?)", (mid, dumps(payload), now()))

    async def get_prompt_debug(self, mid: str) -> dict | None:
        row = await self.db.one("SELECT payload FROM prompt_debug WHERE message_id=?", (mid,))
        return json.loads(row["payload"]) if row else None

    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self.db.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, dumps(value)))

    # --- internals ----------------------------------------------------------------------
    async def _update(self, table: str, rid: str, fields: dict, allowed: set[str], touch: bool = True) -> None:
        fields = {k: v for k, v in fields.items() if k in allowed}
        if touch:
            fields["updated_at"] = now()
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), rid))
