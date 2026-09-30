"""Hybrid search over a project index (lexical + vector, fused with RRF).

Returns OpenSearch-shaped hits so application/UI code stays backend-agnostic.
"""
from __future__ import annotations

from .embeddings import cosine, embedder, lexical_overlap, rrf, tokenize
from .store import store


def search(project_id: str, query: str, top_k: int = 10) -> dict:
    vectors, chunk_map, manifest = store.load_index(project_id)
    if not chunk_map:
        return {"hits": {"total": {"value": 0, "relation": "eq"}, "max_score": 0.0, "hits": []}}

    # Guard against embedding-model mismatch (explicit error, never silent).
    idx_model = manifest.get("embedding", {}).get("model")
    if idx_model and idx_model != embedder.model:
        raise ValueError(f"embedding model mismatch: index={idx_model} query={embedder.model}")

    qvec = embedder.embed(query)
    qtokens = set(tokenize(query))

    vector_scores = {cid: cosine(qvec, v) for cid, v in vectors.items()}
    lexical_scores = {cid: lexical_overlap(qtokens, ch["text"]) for cid, ch in chunk_map.items()}

    vector_rank = [c for c, _ in sorted(vector_scores.items(), key=lambda x: x[1], reverse=True)]
    lexical_rank = [c for c, s in sorted(lexical_scores.items(), key=lambda x: x[1], reverse=True) if s > 0]

    fused = rrf([vector_rank, lexical_rank])
    ordered = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]

    hits = []
    for cid, score in ordered:
        ch = chunk_map.get(cid, {})
        hits.append({
            "_index": f"project-{project_id}",
            "_id": cid,
            "_score": round(score, 6),
            "_source": {
                "document_id": ch.get("document_id"),
                "text": ch.get("text", "")[:500],
                "page_start": ch.get("page_start"),
                "page_end": ch.get("page_end"),
                "section_path": ch.get("section_path", []),
                "block_ids": ch.get("block_ids", []),
                "method": "hybrid_rrf",
                "vector_score": round(vector_scores.get(cid, 0.0), 4),
                "lexical_score": round(lexical_scores.get(cid, 0.0), 4),
            },
        })
    max_score = hits[0]["_score"] if hits else 0.0
    return {"hits": {"total": {"value": len(hits), "relation": "eq"},
                     "max_score": max_score, "hits": hits}}
