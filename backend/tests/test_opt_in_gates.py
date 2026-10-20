"""Opt-in gates: the slow, bounded async + alpha-loop regimes as pytest.

These are DESELECTED by default (see pyproject `addopts = -m 'not opt_in'`).
Run them on demand:

    pytest -m opt_in

They assert the hard invariants the regimes establish (claim safety, process
scaling, fault isolation, matcher correctness), so a regression in the async
stack or the matcher is catchable on demand without slowing the normal suite.
All underlying regimes are bounded (deadlines, join timeouts) — cannot hang.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

pytestmark = pytest.mark.opt_in


def test_async_regime_invariants():
    from alpha_loop import async_regime as ar
    # hard invariant: no double-claim under concurrent workers
    safety = ar.test_claim_safety(workers=8, jobs=300)
    assert safety["no_double_claim"], safety
    # retry recovers
    assert ar.test_retry()["recovered"]
    # per-hop latency is sub-ms (not the old 200ms artifact)
    lat = ar.test_per_hop_latency(n=20)
    assert lat["full_roundtrip_ms"] < 10.0, lat


def test_process_scaling_and_safety():
    from alpha_loop import proc_worker as pw
    res = pw.bench(jobs=120, n=120000, proc_counts=(1, 2, 4))
    assert res["no_double_claim_across_processes"], res
    # processes must scale CPU work (strictly better than the flat thread result)
    assert res["scaling_vs_1"][2] > 1.4, res["scaling_vs_1"]


def test_expanded_fault_isolation_and_backpressure():
    from alpha_loop import async_expanded as ax
    fi = ax.fault_isolation()
    assert fi["queue_intact"], fi
    bp = ax.backpressure(duration_s=1.0)
    assert bp["bounded"], bp


# NOTE: the alpha-loop MATCHER ladder (all 12 benign->hostile stages + the
# zero-false-match invariant) lives in its own module, test_alpha_ladder.py,
# to keep this file focused on the ASYNC/worker regimes. Both are opt_in.
