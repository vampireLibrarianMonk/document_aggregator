"""Optimization bench: correctness gate + speed measurement, reusable per variant.

Discipline for the obsessive-optimization run:
  1. CORRECTNESS GATE (hard): a candidate fuzzy scorer must produce IDENTICAL
     final mappings to the baseline across (A) all alpha_frontier mutation axes
     at every severity on the game schema, and (B) a wide synthetic sweep. Any
     divergence -> the variant is REJECTED (reverted), speed irrelevant.
  2. SPEED: only for gate-passing variants, median infer_mapping time across the
     width sweep, reported as a speedup vs the baseline fuzzy scorer.

A "candidate" is just a replacement `_fuzzy_score(s_name:set, tgt)->float`. We
patch mapping._fuzzy_score, run the SAME infer_mapping, and compare.

Run:  python -m alpha_loop.optbench            (from backend/)
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.json_alignment import mapping  # noqa: E402
from app.json_alignment.mapping import infer_mapping  # noqa: E402
from app.json_alignment.pipeline import run_alignment  # noqa: E402
from app.json_alignment.source_profile import profile_source  # noqa: E402
from app.json_alignment.target_profile import extract_target  # noqa: E402
from alpha_frontier.harness import CLEAN_POOL  # noqa: E402
from alpha_frontier.mutations import AXES  # noqa: E402
from alpha_loop.wide import WideSpec, build_schema, make_wide_doc  # noqa: E402

_GAME = json.loads(
    (Path(__file__).resolve().parents[2]
     / "sample_docs" / "project" / "7" / "golden" / "target_schema.json").read_text())


def _key(m) -> tuple:
    return (tuple(sorted(m.mapped().items())),
            tuple(sorted(m.needs_review())),
            tuple(sorted(m.conflicts())),
            # also lock candidate ORDER per target (a blind spot: reordered
            # candidates can flip a downstream conflict/tie decision).
            tuple((fm.target, tuple(p for p, _s in fm.candidates))
                  for fm in m.fields))


def _frontier_cases():
    tgt = extract_target(_GAME)
    cases = []
    for ax_name, ax in AXES.items():
        for sev in range(ax.max_severity + 1):
            bases = [{}] if ax.base_independent else CLEAN_POOL
            for bi, base in enumerate(bases):
                mr = ax.fn(dict(base), sev, random.Random(f"{ax_name}:{sev}:{bi}"))
                cases.append((f"{ax_name}@s{sev}#{bi}", profile_source([mr.record]), tgt))
    return cases


def _wide_cases():
    cases = []
    for w in (8, 25, 50, 100, 200, 400):
        for d in (0, 4):
            spec = WideSpec(width=w, depth=d)
            doc = make_wide_doc(spec, n_records=3)
            cases.append((f"w{w}d{d}",
                          profile_source(doc.records, doc.descriptions or None),
                          extract_target(build_schema(spec))))
    return cases


def correctness_gate(candidate: Callable | None, *, verbose: bool = False) -> bool:
    """True iff `candidate` fuzzy scorer yields IDENTICAL mappings to baseline
    on every frontier + wide case. candidate=None tests the baseline against
    itself (sanity: must be True)."""
    cases = _frontier_cases() + _wide_cases()
    baseline_prev = mapping._fuzzy_score
    # golden = baseline results
    golden = {}
    for name, src, tgt in cases:
        golden[name] = _key(infer_mapping(src, tgt))
    # candidate results
    ok = True
    if candidate is not None:
        mapping._fuzzy_score = candidate
        try:
            for name, src, tgt in cases:
                if _key(infer_mapping(src, tgt)) != golden[name]:
                    ok = False
                    if verbose:
                        print(f"  DIVERGENCE at {name}")
        finally:
            mapping._fuzzy_score = baseline_prev
    return ok


def speed(candidate: Callable | None, *, widths=(100, 200, 400), repeats=5) -> dict:
    """Median infer_mapping time (ms) per width for the given fuzzy scorer."""
    out = {}
    prev = mapping._fuzzy_score
    if candidate is not None:
        mapping._fuzzy_score = candidate
    try:
        for w in widths:
            spec = WideSpec(width=w, depth=0)
            doc = make_wide_doc(spec, n_records=3)
            src = profile_source(doc.records, doc.descriptions or None)
            tgt = extract_target(build_schema(spec))
            ts = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                infer_mapping(src, tgt)
                ts.append(time.perf_counter() - t0)
            out[w] = statistics.median(ts) * 1000
    finally:
        mapping._fuzzy_score = prev
    return out


def report(name: str, candidate: Callable | None) -> bool:
    gate = correctness_gate(candidate, verbose=True)
    base = speed(None)
    cand = speed(candidate)
    print(f"\n== {name} ==")
    print(f"  correctness gate: {'PASS' if gate else 'FAIL (REJECTED)'}")
    for w in sorted(base):
        sp = base[w] / cand[w] if cand[w] else 0
        print(f"  w={w:>3}: baseline={base[w]:8.2f}ms  candidate={cand[w]:8.2f}ms  speedup={sp:5.2f}x")
    return gate


if __name__ == "__main__":
    # Baseline self-check.
    print("baseline self-consistency:", correctness_gate(None))
