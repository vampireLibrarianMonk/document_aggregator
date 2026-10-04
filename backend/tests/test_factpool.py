"""Pool-backed page-growth tests.

Each page withdraws a distinct record from a real-report-seeded fact pool, so
pages have genuinely distinct ground truth (not a shared corpus). Assert:
  - the pool caps page count (page limit = source capacity); over-capacity is
    refused cleanly with PoolExhaustedError;
  - per-page resolved values are distinct across pages (page-scoped retrieval);
  - no fabrication and contiguous numbering still hold at every size.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import project as sc  # noqa: E402
from app.knowledge.factpool import FactPool, PoolExhaustedError  # noqa: E402
from app.knowledge.pagegrow import build_multipage_from_pool  # noqa: E402
from app.reconcile import reconcile  # noqa: E402

RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}
POOL_SCENARIOS = ["1", "2", "3", "4", "5"]


def _build(sid: str, pages: int, seed: int = 7) -> dict:
    pool = FactPool.load(sid, seed=seed)
    return build_multipage_from_pool(
        sc.load_manifest(sid), sc.load_template(sid), sc.load_first_attempt(sid, "draft"),
        sc.load_graphics(sid), pool, pages, seed=seed,
    )


def _reconcile(b: dict) -> dict:
    return reconcile(b["draft"], b["corpus"], b["graphics"], b["corrections"],
                     b["template"], b["project"]).model_dump()


@pytest.mark.parametrize("sid", POOL_SCENARIOS)
def test_pool_has_capacity(sid):
    assert FactPool.load(sid).capacity() >= 2


@pytest.mark.parametrize("sid", POOL_SCENARIOS)
def test_over_capacity_refused(sid):
    cap = FactPool.load(sid).capacity()
    pool = FactPool.load(sid, seed=7)
    with pytest.raises(PoolExhaustedError):
        build_multipage_from_pool(
            sc.load_manifest(sid), sc.load_template(sid), sc.load_first_attempt(sid, "draft"),
            sc.load_graphics(sid), pool, cap + 1, seed=7,
        )


@pytest.mark.parametrize("sid", POOL_SCENARIOS)
def test_per_page_ground_truth_distinct(sid):
    cap = FactPool.load(sid).capacity()
    b = _build(sid, cap)
    # Each page's corpus doc is distinct, and the withdrawn records are distinct.
    assert len(b["corpus"]) == cap
    sites = [rec["site"] for rec in b["records"]]
    assert len(set(sites)) == cap, "withdrawn records must be distinct per page"


@pytest.mark.parametrize("sid", POOL_SCENARIOS)
def test_resolved_duration_distinct_across_pages(sid):
    """A page-scoped field (duration) should resolve to that page's value, so
    across pages we see the per-record variety, not one repeated value."""
    cap = FactPool.load(sid).capacity()
    b = _build(sid, cap)
    r = _reconcile(b)
    durations = []
    for s in r["sections"]:
        for f in s["fields"]:
            if f["key"].split(".")[-1] == "duration" and f["value"]:
                durations.append(f["value"])
    # The pool's durations are varied; resolved values should reflect >1 distinct.
    assert len(set(durations)) >= 2, f"durations not page-distinct: {durations}"


@pytest.mark.parametrize("sid", POOL_SCENARIOS)
def test_no_fabrication_and_contiguous(sid):
    cap = FactPool.load(sid).capacity()
    r = _reconcile(_build(sid, cap))
    for s in r["sections"]:
        for f in s["fields"]:
            if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
                p = f["provenance"]
                assert p.get("corpus") or p.get("corrections") or p.get("rule")
    nums = [g["figure_number"] for s in r["sections"] for g in s["graphics"]
            if g["figure_number"] is not None]
    assert nums == list(range(1, len(nums) + 1))
