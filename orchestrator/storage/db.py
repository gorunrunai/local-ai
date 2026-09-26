"""Async SQLite access (aiosqlite) with the sqlite-vec extension loaded."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
import numpy as np
import sqlite_vec

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:20]}"


def now() -> float:
    return time.time()


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def vec_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


class Database:
    """One connection per database. SQLite serializes writes; WAL lets reads proceed."""

    def __init__(self, path: str | Path, dims: int):
        self.path = str(path)
        self.dims = dims
        self.conn: aiosqlite.Connection | None = None

    @property
    def in_memory(self) -> bool:
        return self.path == ":memory:"

    async def open(self) -> Database:
        if not self.in_memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.enable_load_extension(True)
        await self.conn.load_extension(sqlite_vec.loadable_path())
        await self.conn.enable_load_extension(False)
        if not self.in_memory:
            await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(SCHEMA)
        for table in ("memories_vec", "chunks_vec"):
            await self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0("
                f"embedding float[{self.dims}] distance_metric=cosine)")
        await self.conn.commit()
        return self

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    # --- helpers ------------------------------------------------------------------------
    async def execute(self, sql: str, params: tuple | dict = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        await self.conn.executemany(sql, rows)
        await self.conn.commit()

    async def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = await self.one(sql, params)
        return next(iter(row.values())) if row else None
