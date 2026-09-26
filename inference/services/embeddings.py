"""Embedding and reranking services (sentence-transformers on MPS)."""

from __future__ import annotations

import numpy as np

from inference.services.base import TORCH_EXECUTOR, LazyService


def _device(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class Embedder(LazyService):
    kind = "embeddings"
    executor = TORCH_EXECUTOR

    def _load(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.spec.repo, device=_device(self.spec.device), local_files_only=True)

    def _unload(self) -> None:
        super()._unload()
        _empty_mps_cache()

    @staticmethod
    def _embed_sync(model, texts: list[str], is_query: bool) -> np.ndarray:
        prompt_name = "query" if is_query and "query" in (model.prompts or {}) else None
        vecs = model.encode(texts, batch_size=16, normalize_embeddings=True,
                            convert_to_numpy=True, prompt_name=prompt_name)
        return vecs.astype(np.float32)

    async def embed(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.spec.dims or 0), dtype=np.float32)
        return await self.run(self._embed_sync, texts, is_query)


class Reranker(LazyService):
    kind = "reranker"
    executor = TORCH_EXECUTOR

    def _load(self):
        from sentence_transformers import CrossEncoder

        return CrossEncoder(self.spec.repo, device=_device(self.spec.device), local_files_only=True)

    def _unload(self) -> None:
        super()._unload()
        _empty_mps_cache()

    @staticmethod
    def _rank_sync(model, query: str, docs: list[str]) -> list[float]:
        scores = model.predict([(query, d) for d in docs], batch_size=16)
        return [float(s) for s in np.asarray(scores).reshape(-1)]

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        return await self.run(self._rank_sync, query, docs)


def _empty_mps_cache() -> None:
    try:
        import torch

        torch.mps.empty_cache()
    except Exception:  # noqa: BLE001, S110 - best effort
        pass
