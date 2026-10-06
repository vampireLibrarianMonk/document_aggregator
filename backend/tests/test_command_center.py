"""Tests for the promoted command-center orchestrator (app/command_center/).

The coordinator reorganizes HOW the correction pipeline runs (sub-task DAG +
deterministic queue assembler + convergence loop) but not WHAT it computes — it
calls the same production refine + reconcile. These tests pin that contract:

  parity        coordinator output == direct run_reconciliation (generated_at
                stripped) on every sample project and both pathways.
  invariants    statuses legal; genuine conflicts preserved; no fabrication.
  determinism   the coordinator path is byte-identical on re-run.
  units         QueueAssembler assembles in stable order regardless of finish
                order; convergence detection is correct.

Offline + deterministic by default (conftest pins Bedrock off).
"""
from __future__ import annotations

import json

import pytest
from app.command_center import (
    QueueAssembler,
    SubTask,
    TaskResult,
    assess,
    count_states,
    run_correction,
)

ALLOWED = {"unchanged", "filled", "corrected", "needs_review", "conflict"}
RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _strip(report: dict) -> str:
    r = dict(report)
    r.pop("generated_at", None)
    return json.dumps(r, sort_keys=True, default=str)


def _all_units(report: dict) -> list[dict]:
    out: list[dict] = []
    for sec in report.get("sections", []):
        out.extend(sec.get("fields", []))
        out.extend(sec.get("graphics", []))
        out.extend(sec.get("tables", []))
    fur = report.get("furniture", {})
    out.extend(fur.get("elements", []))
    out.extend(fur.get("cross_references", []))
    return out


# --------------------------------------------------------------------------
# Parity: the coordinator path equals the direct path, exactly.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["draft", "template"])
def test_coordinator_matches_direct(project_module, project_id, mode):
    direct = project_module.run_reconciliation(mode, project_id, "json",
                                              engine="direct")
    coord = project_module.run_reconciliation(mode, project_id, "json",
                                              engine="coordinator")
    assert _strip(direct) == _strip(coord), (
        f"coordinator diverged from direct on project {project_id} / {mode}")


def test_run_correction_entry_matches_direct(project_module):
    """The run_correction convenience entry equals the direct path too."""
    direct = project_module.run_reconciliation("draft", "1", "json", engine="direct")
    coord = run_correction("1", mode="draft", source_format="json")
    assert _strip(direct) == _strip(coord)


# --------------------------------------------------------------------------
# Invariants on the coordinator output (same guarantees as the engine).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["draft", "template"])
def test_coordinator_statuses_legal(project_module, project_id, mode):
    report = project_module.run_reconciliation(mode, project_id, "json",
                                              engine="coordinator")
    for u in _all_units(report):
        assert u["status"] in ALLOWED, f"illegal status {u['status']}"


def test_coordinator_preserves_conflict(project_module):
    """A unit with disagreeing corrections stays 'conflict' via the coordinator."""
    corrections = project_module.load_corrections("1")
    by_target: dict[str, set] = {}
    for c in corrections:
        by_target.setdefault(c["target"], set()).add(c.get("new_value"))
    conflicting = {t for t, v in by_target.items() if len(v) > 1}
    assert conflicting, "project 1 is expected to carry a conflict"

    report = project_module.run_reconciliation("draft", "1", "json",
                                              engine="coordinator")
    units = {u["key"]: u for u in _all_units(report) if "key" in u}
    for t in conflicting:
        u = units.get(t)
        if u is not None:
            assert u["status"] == "conflict"


def test_coordinator_no_fabrication(project_module, project_id):
    """Every resolved value via the coordinator is provenance-backed."""
    report = project_module.run_reconciliation("draft", project_id, "json",
                                              engine="coordinator")
    for u in _all_units(report):
        if u["status"] in RESOLVED_WITH_VALUE and u.get("value") not in (None, ""):
            p = u.get("provenance", {})
            assert p.get("corpus") or p.get("corrections") or p.get("rule"), (
                f"unprovenanced value: {u.get('key')}={u.get('value')!r}")


def test_coordinator_deterministic(project_module):
    a = project_module.run_reconciliation("draft", "1", "json", engine="coordinator")
    b = project_module.run_reconciliation("draft", "1", "json", engine="coordinator")
    assert _strip(a) == _strip(b)


# --------------------------------------------------------------------------
# Unit: deterministic assembler + convergence detection.
# --------------------------------------------------------------------------

def test_queue_assembler_orders_by_task_order_not_finish():
    """Results come back sorted by (order, task_id) regardless of dispatch
    order, which is the deterministic-assembly guarantee."""
    tasks = [SubTask(id=f"t{i}", kind="k", order=i) for i in (3, 0, 2, 1)]

    def dispatch(t: SubTask, _ctx: dict) -> TaskResult:
        return TaskResult(t.id, t.kind, t.order, output=t.order)

    results = QueueAssembler(parallel=False).run_batch(tasks, {}, dispatch)
    assert [r.order for r in results] == [0, 1, 2, 3]

    # Parallel must assemble to the same order.
    presults = QueueAssembler(parallel=True).run_batch(tasks, {}, dispatch)
    assert [r.order for r in presults] == [0, 1, 2, 3]


def test_convergence_assess():
    report = {"sections": [{"fields": [
        {"status": "corrected"}, {"status": "needs_review"},
        {"status": "conflict"}]}]}
    c = count_states(report)
    assert c["needs_review"] == 1 and c["conflict"] == 1 and c["corrected"] == 1

    s0 = assess(report, prev_needs_review=None)
    assert s0.needs_review == 1 and not s0.converged and s0.progressed

    done = {"sections": [{"fields": [{"status": "corrected"}]}]}
    s1 = assess(done, prev_needs_review=1)
    assert s1.converged and s1.needs_review == 0 and s1.progressed
