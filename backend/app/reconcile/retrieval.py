"""Retrieval-backed corpus lookup for reconciliation.

The regex extractor in corpus_facts.py resolves *known* fields deterministically.
This module adds the retrieval half the spec calls for: for a given section (or
query) it finds the most semantically relevant corpus chunk using the same local
embedder the search stage uses. That gives every filled section a retrieval-based
provenance anchor and makes fact-location robust to corpus rewording, rather than
relying solely on hardcoded patterns.

Uses the shared EmbeddingProvider, so it inherits whichever backend is active
(sentence-transformers by default, hashing in a pure air-gap).
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass

from ..embeddings import cosine, embedder


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_doc: str
    text: str
    score: float


def chunk_corpus(corpus: dict[str, str], granularity: str = "paragraph") -> list[RetrievedChunk]:
    """Split corpus docs into chunks with stable IDs (chunk once).

    granularity="paragraph" for block-level context (section bodies, tables);
    granularity="sentence" for pinpoint fact lookup (discrete fields), so
    retrieval lands on the exact sentence rather than a whole paragraph that may
    contain several competing values.
    """
    chunks: list[RetrievedChunk] = []
    for fname, text in corpus.items():
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        for i, b in enumerate(blocks):
            block_text = " ".join(b.split())
            if granularity == "sentence":
                for j, sent in enumerate(_split_sentences(block_text)):
                    chunks.append(RetrievedChunk(
                        chunk_id=f"{fname}#c{i}s{j}", source_doc=fname, text=sent, score=0.0,
                    ))
            else:
                chunks.append(RetrievedChunk(
                    chunk_id=f"{fname}#c{i}", source_doc=fname, text=block_text, score=0.0,
                ))
    return chunks


def _split_sentences(text: str) -> list[str]:
    # Split on sentence terminators, list-bullet boundaries, and before an
    # inline "Label:" field (so a run of "Date: .. Site: .. Author: .." splits
    # into separate labelled units for pinpoint retrieval).
    parts = _re.split(r"(?<=[.!?])\s+|\s*[-•]\s+|\s+(?=[A-Z][a-z]+:\s)", text)
    return [p.strip() for p in parts if p and p.strip()]


class CorpusRetriever:
    """Embeds corpus chunks once; answers top-k semantic queries. Holds both a
    paragraph index (context) and a sentence index (pinpoint facts)."""

    def __init__(self, corpus: dict[str, str]) -> None:
        self.chunks = chunk_corpus(corpus, "paragraph")
        texts = [c.text for c in self.chunks]
        self._vecs = embedder.embed_batch(texts) if texts else []
        self.sent_chunks = chunk_corpus(corpus, "sentence")
        sent_texts = [c.text for c in self.sent_chunks]
        self._sent_vecs = embedder.embed_batch(sent_texts) if sent_texts else []

    def top(self, query: str, k: int = 1, granularity: str = "paragraph") -> list[RetrievedChunk]:
        chunks = self.sent_chunks if granularity == "sentence" else self.chunks
        vecs = self._sent_vecs if granularity == "sentence" else self._vecs
        if not chunks:
            return []
        qv = embedder.embed(query)
        scored = [
            RetrievedChunk(c.chunk_id, c.source_doc, c.text, cosine(qv, v))
            for c, v in zip(chunks, vecs)
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]

    def best_source(self, query: str) -> str | None:
        hits = self.top(query, k=1)
        return hits[0].source_doc if hits else None
