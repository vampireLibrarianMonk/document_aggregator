"""Bake-off runner: execute all approaches across all 6 projects, score each
against the gold standard, and print a scorecard. Writes a machine-readable
summary to backend/tests/bakeoff/results.json too.

Run from backend/:  python -m tests.bakeoff.run
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import approach_a, approach_b, approach_c
from .harness import PROJECT_IDS, Score, load_raw_inputs, score_against_gold


def _run_one(approach: str, pid: str) -> Score:
    raw = load_raw_inputs(pid)
    try:
        if approach == "A: deterministic":
            rep = approach_a.run(raw)
            return score_against_gold(pid, approach, rep, raw,
                                      deterministic=True, offline_capable=True)
        if approach == "B: governor+model":
            rep, used = approach_b.run(raw, use_model=True)
            s = score_against_gold(pid, approach, rep, raw,
                                   deterministic=False, offline_capable=True)
            s.notes.append("model" if used else "offline-fallback")
            return s
        if approach == "B: governor offline":
            rep, _ = approach_b.run(raw, use_model=False)
            return score_against_gold(pid, approach, rep, raw,
                                      deterministic=True, offline_capable=True)
        if approach == "C: simple pass":
            rep = approach_c.run(raw)
            return score_against_gold(pid, approach, rep, raw,
                                      deterministic=True, offline_capable=True)
    except Exception as exc:
        s = Score(project_id=pid, approach=approach)
        s.error = f"{type(exc).__name__}: {exc}"[:200]
        return s
    raise ValueError(approach)


APPROACHES = ["A: deterministic", "B: governor+model", "B: governor offline", "C: simple pass"]


def _determinism_check(approach: str, pid: str) -> bool:
    """Run twice and compare (skip the model path — nondeterministic by nature)."""
    if approach == "B: governor+model":
        return False
    raw = load_raw_inputs(pid)
    try:
        if approach == "A: deterministic":
            a, b = approach_a.run(raw), approach_a.run(raw)
        elif approach == "B: governor offline":
            a, _ = approach_b.run(raw, use_model=False)
            b, _ = approach_b.run(raw, use_model=False)
        else:
            a, b = approach_c.run(raw), approach_c.run(raw)
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    except Exception:
        return False


def main() -> int:
    all_scores: dict[str, list[Score]] = {a: [] for a in APPROACHES}
    for approach in APPROACHES:
        for pid in PROJECT_IDS:
            all_scores[approach].append(_run_one(approach, pid))

    # Aggregate.
    print("\n" + "=" * 92)
    print("PHASE 3 BAKE-OFF — raw uploaded docs -> corrected intermediate JSON (scored vs gold)")
    print("=" * 92)
    header = (f"{'approach':<22}{'value%':>8}{'status%':>9}{'fabric':>8}"
              f"{'conflicts':>11}{'determ':>8}{'offline':>9}{'errors':>8}")
    print(header)
    print("-" * 92)

    rows = []
    for approach in APPROACHES:
        scores = all_scores[approach]
        n = len(scores)
        ok = [s for s in scores if not s.error]
        val = sum(s.value_pct for s in ok) / len(ok) if ok else 0.0
        stat = sum(s.status_pct for s in ok) / len(ok) if ok else 0.0
        fab = sum(s.fabrications for s in ok)
        conf = sum(1 for s in ok if s.conflict_preserved)
        det = all(_determinism_check(approach, pid) for pid in PROJECT_IDS)
        off = all(s.offline_capable for s in scores)
        errs = sum(1 for s in scores if s.error)
        print(f"{approach:<22}{val:>7.1f}%{stat:>8.1f}%{fab:>8}"
              f"{conf:>8}/{n:<2}{str(det):>8}{str(off):>9}{errs:>8}")
        rows.append({"approach": approach, "value_pct": round(val, 1),
                     "status_pct": round(stat, 1), "fabrications": fab,
                     "conflicts_preserved": f"{conf}/{n}", "deterministic": det,
                     "offline": off, "errors": errs,
                     "notes": sorted({note for s in scores for note in s.notes}),
                     "per_project_errors": {s.project_id: s.error for s in scores if s.error}})
    print("-" * 92)
    print("value%  = emitted field VALUE matches gold (normalized)")
    print("status% = emitted field STATUS matches gold")
    print("fabric  = emitted non-flagged values NOT grounded in corpus/emails (lower is better; 0 ideal)")
    print("conflicts = projects where the known disagreement stayed 'conflict' (not silently picked)")
    print("determ  = identical output on a re-run (model path excluded by nature)")
    print("=" * 92 + "\n")

    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
