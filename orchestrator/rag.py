"""Retrieval: chunking, indexing and hybrid search (vector + full-text, fused, reranked)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from inference.services.embeddings import Embedder, Reranker
from orchestrator.storage.store import Store

log = logging.getLogger(__name__)
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
RRF_K = 60
RERANK_CANDIDATES = 16


@dataclass
class Hit:
    chunk_id: int
    text: str
    source_type: str
    source_id: str
    conversation_id: str | None
    meta: dict
    score: float


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph, then sentence boundaries; pack into ~size-char chunks with overlap."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= size:
            pieces.append(para)
        else:
            pieces.extend(s for s in re.split(r"(?<=[.!?])\s+|\n", para) if s)
    chunks, cur = [], ""
    for p in pieces:
        while len(p) > size:  # a single giant "sentence" (tables, minified code)
            chunks.append(p[:size])
            p = p[size - overlap:]
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p if overlap else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        chunks.append(cur)
    return chunks


class Retriever:
    def __init__(self, store: Store, embedder: Embedder, reranker: Reranker | None):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    async def index(self, source_type: str, source_id: str, sections: list[tuple[str, str]],
                    project_id: str | None = None, conversation_id: str | None = None,
                    extra_meta: dict | None = None, replace: bool = False) -> int:
        """Index (label, text) sections. Returns the number of chunks written."""
        if replace:
            await self.store.delete_chunks(source_type, source_id)
        elif await self.store.has_chunks(source_type, source_id):
            return 0
        rows = []
        for label, text in sections:
            for i, chunk in enumerate(chunk_text(text)):
                rows.append({"source_type": source_type, "source_id": source_id, "project_id": project_id,
                             "conversation_id": conversation_id, "ord": len(rows), "text": chunk,
                             "meta": {"label": label, "part": i, **(extra_meta or {})}})
        if not rows:
            return 0
        embs = await self.embedder.embed([r["text"] for r in rows])
        await self.store.add_chunks(rows, embs)
        return len(rows)

    async def search(self, query: str, scope: dict, k: int = 6, rerank: bool = True) -> list[Hit]:
        qv = (await self.embedder.embed([query], is_query=True))[0]
        vec = await self.store.search_chunks_vec(qv, scope, k=20)
        fts = await self.store.search_chunks_fts(query, scope, k=20)
        fused: dict[int, dict] = {}
        scores: dict[int, float] = {}
        for ranked in (vec, fts):
            for rank, row in enumerate(ranked):
                fused[row["id"]] = row
                scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(scores, key=scores.get, reverse=True)[:RERANK_CANDIDATES]
        if rerank and self.reranker and len(order) > k:
            try:
                rr = await self.reranker.rerank(query, [fused[i]["text"] for i in order])
                order = [i for _, i in sorted(zip(rr, order, strict=True), reverse=True)]
                scores = dict(zip(order, sorted(rr, reverse=True), strict=True))
            except Exception:
                log.exception("rerank failed")
        return [Hit(chunk_id=i, text=fused[i]["text"], source_type=fused[i]["source_type"],
                    source_id=fused[i]["source_id"], conversation_id=fused[i]["conversation_id"],
                    meta=fused[i]["meta"] if isinstance(fused[i]["meta"], dict) else {},
                    score=round(float(scores[i]), 4)) for i in order[:k]]


class MemoryService:
    """User memory: short facts, embedded for per-turn retrieval."""

    DUPLICATE_DISTANCE = 0.08   # cosine distance below which a new fact updates an old one
    RELEVANT_DISTANCE = 0.62    # retrieval cutoff

    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    async def save(self, text: str, conversation_id: str | None) -> dict:
        text = text.strip()
        emb = (await self.embedder.embed([text]))[0]
        near = await self.store.search_memories(emb, k=1)
        if near and near[0]["distance"] < self.DUPLICATE_DISTANCE:
            return {**await self.store.update_memory(near[0]["id"], text, emb), "updated": True}
        return {**await self.store.add_memory(text, emb, conversation_id), "updated": False}

    async def search(self, query: str, k: int = 8, max_distance: float | None = None) -> list[dict]:
        if not await self.store.db.scalar("SELECT 1 FROM memories LIMIT 1"):
            return []
        emb = (await self.embedder.embed([query], is_query=True))[0]
        cutoff = self.RELEVANT_DISTANCE if max_distance is None else max_distance
        return [m for m in await self.store.search_memories(emb, k=k) if m["distance"] <= cutoff]

    async def update(self, mid: str, text: str) -> dict | None:
        emb = (await self.embedder.embed([text]))[0]
        return await self.store.update_memory(mid, text, emb)
