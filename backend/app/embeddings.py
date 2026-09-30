"""Embedding providers + hybrid-retrieval helpers.

The reference spec (§49, §54, Embedding Strategy) requires embeddings to sit
behind a swappable interface so no single model becomes load-bearing, and the
default local option to be an open-weight, permissive, CPU-friendly, air-gap
model. Two providers implement one protocol:

    SentenceTransformerProvider  real open-weight model (all-MiniLM-L6-v2, Apache-2.0)
    HashingEmbeddingProvider     zero-dependency deterministic fallback

Selection is by EMBEDDING_BACKEND (auto|sentence-transformers|hashing) with
graceful fallback: if sentence-transformers or its weights are unavailable, the
pipeline still runs on the hashing embedder. Choosing between them is a MEASURED
decision — see backend/benchmark_embeddings.py (Recall@K / MRR / nDCG).
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .config import settings

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Stable contract. Vectors are unit-normalized so cosine == dot product."""
    model: str
    revision: str
    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """Deterministic hashing bag-of-words. No downloads, no GPU. Weak on meaning
    but keeps the embed/vector-search stages functional in a pure air-gap."""

    def __init__(self) -> None:
        self.model = settings.EMBEDDING_MODEL
        self.revision = settings.EMBEDDING_REVISION
        self.dim = settings.EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerProvider:
    """Real open-weight model via sentence-transformers, CPU, normalized output.
    Lazy-loads the model on first use so importing this module stays cheap."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model = model_name or settings.EMBEDDING_ST_MODEL
        self.revision = "sentence-transformers"
        self._st = None
        self._dim: int | None = None

    def _ensure(self) -> None:
        if self._st is None:
            from sentence_transformers import SentenceTransformer  # lazy

            self._st = SentenceTransformer(self.model, device="cpu")
            get_dim = getattr(self._st, "get_embedding_dimension", None) or \
                self._st.get_sentence_embedding_dimension
            self._dim = int(get_dim())

    @property
    def dim(self) -> int:
        self._ensure()
        assert self._dim is not None
        return self._dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._ensure()
        assert self._st is not None
        vecs = self._st.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [v.tolist() for v in vecs]


def _sentence_transformers_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


def make_embedder(backend: str | None = None) -> EmbeddingProvider:
    """Factory honoring EMBEDDING_BACKEND with graceful fallback."""
    choice = (backend or settings.EMBEDDING_BACKEND).lower()
    if choice in ("sentence-transformers", "st"):
        return SentenceTransformerProvider()
    if choice == "hashing":
        return HashingEmbeddingProvider()
    # auto
    if _sentence_transformers_available():
        return SentenceTransformerProvider()
    return HashingEmbeddingProvider()


# Default provider used across the app.
embedder: EmbeddingProvider = make_embedder()


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # both are unit-normalized


def lexical_overlap(query_tokens: set[str], text: str) -> float:
    text_tokens = set(tokenize(text))
    if not text_tokens or not query_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def rrf(rankings: Iterable[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion over several ranked ID lists."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return scores
