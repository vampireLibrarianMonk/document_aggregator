"""Embedding benchmark: choose the model by measurement, not by default.

Implements the reference spec's Embedding Strategy: chunk once with stable IDs,
embed the SAME chunks with each candidate provider, and score retrieval on a
human-judged golden set using Recall@K, MRR, and nDCG with exact brute-force
cosine (no index tuning to muddy the comparison). MTEB/BEIR-style intrinsic
retrieval testing, scaled to a report-sized corpus.

    python backend/benchmark_embeddings.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.embeddings import (  # noqa: E402
    HashingEmbeddingProvider,
    SentenceTransformerProvider,
    cosine,
    tokenize,
)

SC = Path(__file__).resolve().parents[1] / "sample_docs" / "scenario" / "corpus"


# ---- Corpus: chunk once, stable IDs (so every model embeds identical text) ----

def build_chunks() -> dict[str, str]:
    """Split the scenario corpus into paragraph-ish chunks with stable IDs."""
    chunks: dict[str, str] = {}
    for fname in ("field_report_2026-03-02.txt", "root_cause_notes_2026-03-15.md"):
        text = (SC / fname).read_text(encoding="utf-8")
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        for i, b in enumerate(blocks):
            chunks[f"{fname}#c{i}"] = " ".join(b.split())
    return chunks


# ---- Golden set: queries with the chunk IDs a human judges relevant ----
# Relevance is by which chunk actually contains the answer.

def golden_set(chunk_ids: list[str]) -> list[dict]:
    def match(substr: str) -> list[str]:
        return [cid for cid in chunk_ids if substr in cid]

    return [
        {"query": "what firmware version was affected", "relevant_contains": "root_cause"},
        {"query": "how long did the outage last packet loss duration", "relevant_contains": "field_report"},
        {"query": "recommended fix rollback firmware hotfix", "relevant_contains": "root_cause"},
        {"query": "cabinet temperature thermal stress buffer", "relevant_contains": "root_cause"},
        {"query": "manual power cycle recovery", "relevant_contains": "field_report"},
        {"query": "network topology relay station figure", "relevant_contains": "field_report"},
    ]


def relevant_ids(spec: dict, chunks: dict[str, str]) -> set[str]:
    """Judge relevance: chunks from the expected source doc that share query terms."""
    q_tokens = set(tokenize(spec["query"]))
    out = set()
    for cid, text in chunks.items():
        if spec["relevant_contains"] not in cid:
            continue
        overlap = len(q_tokens & set(tokenize(text)))
        if overlap >= 2:
            out.add(cid)
    return out


# ---- Metrics ----

def dcg(rels: list[int]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


def evaluate(provider, chunks: dict[str, str], queries: list[dict], k: int = 3) -> dict:
    cids = list(chunks)
    vecs = {cid: provider.embed(chunks[cid]) for cid in cids}

    recall_sum = mrr_sum = ndcg_sum = 0.0
    n = 0
    for spec in queries:
        rel = relevant_ids(spec, chunks)
        if not rel:
            continue
        n += 1
        qv = provider.embed(spec["query"])
        ranked = sorted(cids, key=lambda c: cosine(qv, vecs[c]), reverse=True)
        topk = ranked[:k]

        hit = len(set(topk) & rel)
        recall_sum += hit / len(rel)

        rr = 0.0
        for rank, cid in enumerate(ranked):
            if cid in rel:
                rr = 1.0 / (rank + 1)
                break
        mrr_sum += rr

        gains = [1 if cid in rel else 0 for cid in topk]
        ideal = sorted(gains, reverse=True)
        ndcg_sum += (dcg(gains) / dcg(ideal)) if dcg(ideal) > 0 else 0.0

    return {
        "queries": n,
        f"recall@{k}": round(recall_sum / n, 3),
        "mrr": round(mrr_sum / n, 3),
        f"ndcg@{k}": round(ndcg_sum / n, 3),
    }


def main() -> None:
    chunks = build_chunks()
    queries = golden_set(list(chunks))
    print(f"corpus chunks: {len(chunks)}   golden queries: {len(queries)}\n")

    providers = [("hashing", HashingEmbeddingProvider())]
    try:
        providers.append(("sentence-transformers (all-MiniLM-L6-v2)", SentenceTransformerProvider()))
    except Exception as exc:  # lib missing -> benchmark still runs on hashing
        print(f"(sentence-transformers unavailable: {exc})")

    print(f"{'provider':45s} {'recall@3':>9s} {'mrr':>6s} {'ndcg@3':>7s}")
    print("-" * 70)
    results = {}
    for name, prov in providers:
        m = evaluate(prov, chunks, queries, k=3)
        results[name] = m
        print(f"{name:45s} {m['recall@3']:>9} {m['mrr']:>6} {m['ndcg@3']:>7}")

    if len(results) == 2:
        names = list(results)
        base, cand = results[names[0]], results[names[1]]
        print(f"\ndelta mrr: {round(cand['mrr'] - base['mrr'], 3):+}   "
              f"delta ndcg@3: {round(cand['ndcg@3'] - base['ndcg@3'], 3):+}   "
              f"delta recall@3: {round(cand['recall@3'] - base['recall@3'], 3):+}")
        winner = names[1] if cand["ndcg@3"] >= base["ndcg@3"] else names[0]
        print(f"measured winner (ndcg@3): {winner}")


if __name__ == "__main__":
    main()
