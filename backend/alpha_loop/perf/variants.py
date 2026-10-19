"""Variants — the swappable implementations you game for speed.

A Variant activates a particular pipeline configuration for the duration of a
measured run (and restores the prior state afterward). The first dimension wired
is the EMBEDDING backend (the heaviest real dependency, an existing seam via
`EMBEDDING_BACKEND` + the provider singleton). New dimensions — a different text
normalizer, a different model-calling strategy — register the same way: give a
name and a context manager that installs/undoes the swap.

Design: a Variant is just (name, description, activate()->contextmanager). The
experiment runner activates A, measures, restores; activates B, measures,
restores. Nothing is monkey-patched permanently; every swap is scoped.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass
class Variant:
    name: str
    description: str
    activate: Callable[[], "AbstractContextManager"]  # noqa: F821 (typing hint)


VARIANTS: dict[str, Variant] = {}


def register_variant(name: str, description: str,
                     activate: Callable[[], object]) -> Variant:
    v = Variant(name=name, description=description, activate=activate)  # type: ignore[arg-type]
    VARIANTS[name] = v
    return v


def get_variant(name: str) -> Variant:
    if name not in VARIANTS:
        raise KeyError(f"unknown variant '{name}'; known: {sorted(VARIANTS)}")
    return VARIANTS[name]


# --------------------------------------------------------------------------
# Embedding-backend dimension: hashing (zero-dep, fast) vs sentence-transformers
# (real MiniLM, slower, better semantics). This is the canonical first A/B: the
# correctness gate will tell us whether the faster hashing backend changes any
# mapping outcome on a given workload (often it does on hard synonym shapes —
# which is exactly the speed/quality tradeoff worth seeing explicitly).
# --------------------------------------------------------------------------

@contextmanager
def _embedding_backend(backend: str) -> Iterator[None]:
    """Scope the EMBEDDING_BACKEND setting + rebuild the module-global embedder
    so the next embed call uses the chosen backend. Restores both on exit.

    Note: the alignment matcher imports `embeddings.embedder` live on each use
    and gates its semantic-embedding tier on the model being MiniLM, so swapping
    this global genuinely turns that tier on (sentence-transformers) or off
    (hashing) for the measured run."""
    from app import embeddings
    from app.config import settings
    from app.json_alignment import mapping as _mapping

    prev_backend = getattr(settings, "EMBEDDING_BACKEND", "auto")
    prev_embedder = embeddings.embedder
    # The matcher probes the backend ONCE per process and caches embeds; reset
    # both so this variant's backend is actually re-detected and used, then
    # restore the caches afterward so the swap leaves no residue.
    prev_probed = _mapping._SEMANTIC_PROBED
    prev_enabled = _mapping._SEMANTIC_ENABLED
    prev_cache = dict(_mapping._embed_cache)
    try:
        settings.EMBEDDING_BACKEND = backend
        embeddings.embedder = embeddings.make_embedder(backend)
        _mapping._SEMANTIC_PROBED = False
        _mapping._SEMANTIC_ENABLED = False
        _mapping._embed_cache.clear()
        yield
    finally:
        settings.EMBEDDING_BACKEND = prev_backend
        embeddings.embedder = prev_embedder
        _mapping._SEMANTIC_PROBED = prev_probed
        _mapping._SEMANTIC_ENABLED = prev_enabled
        _mapping._embed_cache.clear()
        _mapping._embed_cache.update(prev_cache)


def _register_builtin() -> None:
    register_variant(
        "embed:hashing", "Deterministic hashing embedder (zero-dependency, fast)",
        lambda: _embedding_backend("hashing"))
    register_variant(
        "embed:sentence-transformers",
        "Real MiniLM via sentence-transformers (slower, stronger semantics)",
        lambda: _embedding_backend("sentence-transformers"))
    register_variant(
        "infer:baseline", "Production infer_mapping (O(width^2) full scan)",
        _infer_baseline)
    register_variant(
        "infer:hash-indexed",
        "Index-accelerated infer_mapping (token-set/synonym/token/length indexes)",
        _infer_indexed)


# --------------------------------------------------------------------------
# Inference-algorithm dimension: baseline O(width^2) scan vs the hash-indexed
# prototype. Activating a variant swaps the mapping function the pipeline uses
# by monkey-patching `pipeline.infer_mapping` for the scope of the measured run
# (restored on exit). The A/B's correctness gate proves the swap changes no
# mapping outcome; the timing shows the vertical speedup on wide schemas.
# --------------------------------------------------------------------------

@contextmanager
def _patch_infer(fn) -> Iterator[None]:
    from app.json_alignment import pipeline
    prev = pipeline.infer_mapping
    try:
        pipeline.infer_mapping = fn
        yield
    finally:
        pipeline.infer_mapping = prev


def _infer_baseline():
    from app.json_alignment.mapping import infer_mapping
    return _patch_infer(infer_mapping)


@contextmanager
def _patch_infer_and_lev(fn) -> Iterator[None]:
    """Swap infer_mapping AND the fuzzy scorer for the scope. The fast fuzzy
    scorer adds a bigram lower-bound + banded early-exit before the Levenshtein
    DP; it is behavior-identical (same guards/budget/scaling) and only skips the
    DP for pairs it can prove exceed the budget. The A/B correctness gate proves
    the swap changes no mapping outcome."""
    from app.json_alignment import mapping, pipeline
    from .infer_indexed import make_fuzzy_score_fast

    prev_infer = pipeline.infer_mapping
    prev_fuzzy = mapping._fuzzy_score
    try:
        pipeline.infer_mapping = fn
        mapping._fuzzy_score = make_fuzzy_score_fast(mapping)
        yield
    finally:
        pipeline.infer_mapping = prev_infer
        mapping._fuzzy_score = prev_fuzzy


def _infer_indexed():
    from .infer_indexed import infer_mapping_indexed
    return _patch_infer_and_lev(infer_mapping_indexed)


_register_builtin()
