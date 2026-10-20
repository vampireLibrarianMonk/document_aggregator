"""Regression guard for the indexed matcher optimization (committed pytest).

The production `infer_mapping` was rewritten to score only index-selected
candidates (instead of a full source x target scan), and `_fuzzy_score` gained a
bigram lower-bound + banded Levenshtein before the full DP. Both are claimed to
produce IDENTICAL mappings to the original full-scan / full-DP algorithm — the
whole optimization rests on that equivalence.

This test BAKES IN a reference full-scan implementation (the pre-optimization
algorithm) and asserts production matches it byte-for-byte across a diverse set
of mutated inputs (every variation difficulty level + a wide synthetic schema).
If a future edit breaks the index's candidate selection or the fuzzy fast path,
this diverges and fails — turning the manual bench proof into a permanent guard.

It also re-asserts the load-bearing PRECISION invariant: zero fabricated
mappings (no mapped source path that isn't a real source field).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.json_alignment import mapping as M
from app.json_alignment.mapping import (
    STATUS_CONFLICT,
    STATUS_MAPPED,
    STATUS_NEEDS_REVIEW,
    FieldMapping,
    Mapping,
    _cosine_to_target,
    _score_candidate,
    infer_mapping,
)
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target
from app.json_alignment.variation import DIFFICULTY_LEVELS, generate_variation

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _golden() -> list[dict]:
    return json.loads((FIXTURE_DIR / "variations" / "golden_records.json").read_text())


# --------------------------------------------------------------------------
# Reference implementation: the ORIGINAL full-scan grounding loop + full-DP
# fuzzy. Deliberately simple (no index, no banded DP). This is the oracle the
# optimized production code must equal.
# --------------------------------------------------------------------------

def _reference_infer(source, target) -> Mapping:
    src_fields = source.fields
    sf_by_path = {sf.path: sf for sf in src_fields}
    out: list[FieldMapping] = []
    for tgt in target.fields:
        scored = []
        for sf in src_fields:                       # FULL SCAN (no index)
            score, method = _score_candidate(sf, tgt)
            if score > 0.0:
                scored.append((score, method, sf.path))
        scored.sort(key=lambda x: (-x[0], x[2]))
        if not scored:
            out.append(FieldMapping(target=tgt.name, source_path=None,
                       method="abstain", confidence=0.0,
                       status=STATUS_NEEDS_REVIEW, candidates=(),
                       note="no source field cleared the match floor"))
            continue
        top_score, top_method, top_path = scored[0]
        cand_order = scored
        if len(scored) > 1:
            cos = {p: _cosine_to_target(sf_by_path[p], tgt)
                   for _s, _m, p in scored if p in sf_by_path}
            cand_order = sorted(scored, key=lambda x: (-x[0], -cos.get(x[2], 0.0), x[2]))
        candidates = tuple((p, s) for s, _m, p in cand_order)
        tied = [c for c in scored if c[0] == top_score and c[2] != top_path]
        if tied and top_method not in ("exact", "normalized"):
            out.append(FieldMapping(target=tgt.name, source_path=None,
                       method=top_method, confidence=top_score,
                       status=STATUS_CONFLICT, candidates=candidates,
                       note=f"ambiguous: {1 + len(tied)} source fields tie at {top_score:.2f}"))
            continue
        out.append(FieldMapping(target=tgt.name, source_path=top_path,
                   method=top_method, confidence=top_score,
                   status=STATUS_MAPPED, candidates=candidates))
    return Mapping(fields=out)


def _reference_fuzzy(s_name, tgt) -> float:
    """Original fuzzy: full Levenshtein, no bigram prefilter / banding."""
    if not s_name or not tgt.tokens:
        return 0.0
    if len(s_name) == 1:
        (only,) = tuple(s_name)
        if only in M._FUZZY_WORD_BLOCKLIST and only not in set(tgt.tokens):
            return 0.0
    s = "".join(sorted(s_name))
    t = "".join(sorted(tgt.tokens))
    if len(t) < 3 or abs(len(s) - len(t)) > 2:
        return 0.0
    budget = max(1, len(t) // 4)
    d = M._levenshtein(s, t)
    if d == 0 or d > budget:
        return 0.0
    return M._FUZZY_MIN + (1.0 - (d - 1) / budget) * (M._FUZZY_MAX - M._FUZZY_MIN)


def _key(m: Mapping):
    """Full observable fingerprint: mapped + needs_review + conflicts + candidate
    ORDER per target (candidate order can flip a downstream tie/conflict)."""
    return (
        tuple(sorted(m.mapped().items())),
        tuple(sorted(m.needs_review())),
        tuple(sorted(m.conflicts())),
        tuple((fm.target, tuple(p for p, _s in fm.candidates)) for fm in m.fields),
    )


def _wide_schema(width: int) -> tuple[dict, list[dict]]:
    """A flat wide golden schema + source records with synonym/token-disjoint
    keys, exercising the index's fuzzy length-window + token paths at scale."""
    names = [f"field_{i:03d}" for i in range(width)]
    props = {n: {"type": "string", "description": f"the {n}"} for n in names}
    schema = {"title": f"wide{width}", "type": "object",
              "properties": props, "required": names[:2]}
    recs = [{("src_" + n): f"{n}_v{r}" for n in names} for r in range(3)]
    return schema, recs


# --------------------------------------------------------------------------
# Equivalence across every variation difficulty level.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level", sorted(DIFFICULTY_LEVELS))
def test_indexed_matcher_equals_full_scan_per_level(level):
    schema, golden = _schema(), _golden()
    props = list(schema.get("properties", {}))
    required = list(schema.get("required", []))
    knobs = DIFFICULTY_LEVELS[level]
    var = generate_variation(golden, props, required, knobs,
                             name=f"L{level}", level=level, seed=42)
    src = profile_source(var.records, var.descriptions or None)
    tgt = extract_target(schema)

    reference = _reference_infer(src, tgt)     # full scan + (prod) fuzzy
    produced = infer_mapping(src, tgt)          # production (indexed)
    assert _key(produced) == _key(reference), (
        f"indexed matcher diverged from full-scan at difficulty level {level}")


def test_fuzzy_fast_path_equals_full_dp():
    """The production _fuzzy_score (bigram prefilter + banded DP) must return the
    SAME score as the full-DP reference for every (source leaf, target) pair that
    arises across all difficulty levels."""
    schema, golden = _schema(), _golden()
    props = list(schema.get("properties", {}))
    required = list(schema.get("required", []))
    tgt = extract_target(schema)
    mismatches = []
    for level, knobs in DIFFICULTY_LEVELS.items():
        var = generate_variation(golden, props, required, knobs,
                                 name=f"L{level}", level=level, seed=42)
        src = profile_source(var.records, var.descriptions or None)
        for sf in src.fields:
            s_name = set(sf.tokens)
            for tf in tgt.fields:
                fast = M._fuzzy_score(s_name, tf)
                ref = _reference_fuzzy(s_name, tf)
                if abs(fast - ref) > 1e-12:
                    mismatches.append((level, sf.path, tf.name, fast, ref))
    assert not mismatches, f"fuzzy fast path diverged: {mismatches[:5]}"


@pytest.mark.parametrize("width", [8, 50, 120])
def test_indexed_matcher_equals_full_scan_wide(width):
    schema, recs = _wide_schema(width)
    src = profile_source(recs)
    tgt = extract_target(schema)
    assert _key(infer_mapping(src, tgt)) == _key(_reference_infer(src, tgt)), (
        f"indexed matcher diverged from full-scan on a width-{width} schema")


def test_no_fabricated_mappings_across_levels():
    """Load-bearing precision invariant: every mapped source path is a REAL
    source field (no invented correspondence), at every difficulty level."""
    schema, golden = _schema(), _golden()
    props = list(schema.get("properties", {}))
    required = list(schema.get("required", []))
    tgt = extract_target(schema)
    for level, knobs in DIFFICULTY_LEVELS.items():
        var = generate_variation(golden, props, required, knobs,
                                 name=f"L{level}", level=level, seed=42)
        src = profile_source(var.records, var.descriptions or None)
        real_paths = {sf.path for sf in src.fields}
        m = infer_mapping(src, tgt)
        for tgt_name, src_path in m.mapped().items():
            assert src_path in real_paths, (
                f"level {level}: {tgt_name} mapped to non-existent source "
                f"{src_path!r} (fabrication)")
