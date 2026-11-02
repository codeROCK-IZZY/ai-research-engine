"""Provider selection and the local embedding model."""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

from app.ai.base import LLMProvider
from app.ai.extractive import ExtractiveProvider
from app.core.config import get_settings


def get_llm() -> LLMProvider:
    """Claude when ``ANTHROPIC_API_KEY`` is set (or forced), else the offline extractive provider."""
    if get_settings().llm_mode == "claude":
        from app.ai.claude import ClaudeProvider

        return ClaudeProvider()
    return ExtractiveProvider()


class Embedder:
    """Local embedding model (fastembed / ONNX, CPU). Thread-safe lazy load.

    bge models expect a query prefix; fastembed's ``query_embed`` / ``passage_embed``
    apply the right one, so queries and passages land in the same space correctly.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _get(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                s = get_settings()
                self._model = TextEmbedding(
                    self.model_name, cache_dir=s.embedding_cache_dir, threads=s.embedding_threads
                )
            return self._model

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._get().passage_embed(texts, batch_size=32)]

    def query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._get().query_embed(text)))]


@lru_cache
def get_embedder() -> Embedder:
    return Embedder(get_settings().embedding_model)


class Reranker:
    """Cross-encoder re-ranker (fastembed / ONNX, CPU).

    The hybrid retriever is good at *recall* (finding candidates); a cross-encoder reads the
    question and each passage together, which is much better at *precision* (ordering them).
    It is applied to the top candidates only, so its cost stays small.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _get(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                s = get_settings()
                self._model = TextCrossEncoder(
                    self.model_name, cache_dir=s.embedding_cache_dir, threads=s.embedding_threads
                )
            return self._model

    def scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(x) for x in self._get().rerank(query, passages, batch_size=16)]


@lru_cache
def get_reranker() -> Reranker:
    return Reranker(get_settings().reranker_model)
