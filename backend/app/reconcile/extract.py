"""Generic, retrieval-driven fact extraction.

Replaces the project-specific regex extractor. For each field declared in the
project manifest, we:

    1. retrieve the most semantically relevant corpus chunk for the field's query
       (using the shared embedder / indexing), then
    2. apply a small, GENERIC value-extraction rule to that chunk.

The manifest supplies the query and the extract type; the engine supplies no
project knowledge. Extraction rules are generic patterns (a version number, a
date, a duration phrase, a labelled line) — not project-specific literals — so
the same code works across unrelated corpora. When nothing can be extracted the
field is simply absent, and the engine flags it (needs_review), never invents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .retrieval import CorpusRetriever

# Generic value patterns (domain-agnostic).
_VERSION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DURATION = re.compile(r"\b(\w+)\s+(hour|hours|minute|minutes|day|days|week|weeks)\b", re.IGNORECASE)


def _strip_leading_label(text: str, anchor: str | None) -> str:
    """Remove a leading heading label so the body reads as prose."""
    for label in filter(None, [anchor, "Summary", "Observations", "Findings",
                               "Preliminary Assessment", "Assessment"]):
        m = re.match(rf"\s*{re.escape(label)}\s+", text, re.IGNORECASE)
        if m:
            return text[m.end():].strip()
    return text.strip()


@dataclass
class Fact:
    value: str
    source_doc: str
    chunk_id: str
    score: float
    method: str  # retrieval+<extract-type>


def _extract_value(text: str, extract: str, query: str, hint: str | None = None) -> str | None:
    if extract == "token" and hint:
        # Pull the token immediately AFTER the hint phrase (generic: works for
        # non-numeric identifiers like "B2.3" that follow "was running ...").
        m = re.search(rf"{re.escape(hint)}\s+([A-Za-z0-9][\w.\-]*)", text, re.IGNORECASE)
        return m.group(1).rstrip(".,;:") if m else None
    if extract == "line" and hint and hint.endswith(":"):
        # Label-anchored value: take text after the "Label:" up to the next label.
        label = hint.rstrip(":")
        m = re.search(rf"{re.escape(label)}\s*:\s*(.+?)(?:\s{{2,}}|\s+[A-Z][a-z]+:|$)", text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    if extract == "version":
        m = _VERSION.search(text)
        return m.group(1) if m else None
    if extract == "date":
        m = _DATE.search(text)
        return m.group(1) if m else None
    if extract == "duration":
        m = _DURATION.search(text)
        return f"{m.group(1)} {m.group(2)}".lower() if m else None
    if extract == "line":
        # The line most overlapping the query terms.
        q = set(re.findall(r"[a-z0-9]+", query.lower()))
        best, best_score = None, 0
        for line in text.splitlines():
            toks = set(re.findall(r"[a-z0-9]+", line.lower()))
            score = len(q & toks)
            if score > best_score:
                best, best_score = line.strip(), score
        # Strip a leading "Label:" if present.
        if best and ":" in best:
            best = best.split(":", 1)[1].strip()
        return best or None
    # extract == "none": no literal value to pull (e.g. severity absent from corpus)
    return None


class RetrievalExtractor:
    """Manifest-driven fact finder over an indexed corpus."""

    def __init__(self, corpus: dict[str, str]) -> None:
        self.retriever = CorpusRetriever(corpus)

    def field(self, query: str, extract: str, hint: str | None = None,
              source_doc: str | None = None) -> Fact | None:
        # Sentence-level retrieval so we land on the exact sentence containing
        # the fact, not a paragraph mixing several candidate values. An optional
        # manifest `hint` disambiguates competing values; `source_doc` scopes
        # retrieval to a single document (used for per-page fact pools so a
        # page's field only sees its own page's facts).
        hits = self.retriever.top(query, k=8, granularity="sentence")
        if source_doc:
            hits = [h for h in hits if h.source_doc == source_doc]
        if hint:
            hint_low = hint.lower()
            hinted = [h for h in hits if hint_low in h.text.lower()]
            hits = hinted or hits
        for h in hits:
            value = _extract_value(h.text, extract, query, hint)
            if value is not None:
                return Fact(value=value, source_doc=h.source_doc,
                            chunk_id=h.chunk_id, score=round(h.score, 3),
                            method=f"retrieval+{extract}" + ("+hint" if hint else ""))
        return None

    def body(self, query: str, block_anchor: str | None = None,
             source_doc: str | None = None) -> Fact | None:
        hits = self.retriever.top(query, k=8)
        if source_doc:
            hits = [h for h in hits if h.source_doc == source_doc]
        if not hits:
            return None
        chosen = hits[0]
        if block_anchor:
            anchor_low = block_anchor.lower()
            for h in hits:
                if anchor_low in h.text.lower():
                    chosen = h
                    break
        # Strip a leading section label ("Summary ..." / "Preliminary Assessment ...").
        text = _strip_leading_label(chosen.text, block_anchor)
        return Fact(value=text, source_doc=chosen.source_doc, chunk_id=chosen.chunk_id,
                    score=round(chosen.score, 3),
                    method="retrieval+block" + ("+anchor" if block_anchor else ""))

    def rows(self, query: str, row_marker: str, cell_queries: dict[str, str]) -> list[dict]:
        """Find the corpus block for the table, split into rows by row_marker,
        and pull each cell generically as "Label: value" pairs. The set of
        labels to look for comes from the manifest cell_queries plus the marker.
        Cells with no value are omitted (engine marks them needs_review)."""
        hits = self.retriever.top(query, k=5)
        block = next((h for h in hits if row_marker.lower() in h.text.lower()), None)
        if block is None:
            return []
        marker_label = row_marker.rstrip(":")
        # All labels that can appear in a row, used as value terminators.
        labels = [marker_label] + [c for c in cell_queries if c != marker_label]
        term = "|".join(re.escape(x) for x in labels)

        segments = re.split(rf"(?={re.escape(row_marker)})", block.text)
        rows: list[dict] = []
        for seg in segments:
            if row_marker.lower() not in seg.lower():
                continue
            cells: dict[str, str] = {}
            for label in labels:
                m = re.search(
                    rf"{re.escape(label)}\s*:\s*(.+?)\s*(?:\.\s|\.$|(?:{term})\s*:|$)",
                    seg, re.IGNORECASE,
                )
                if m and m.group(1).strip():
                    cells[label] = " ".join(m.group(1).split())
            rows.append({"cells": cells, "source_doc": block.source_doc, "chunk_id": block.chunk_id})
        return rows
