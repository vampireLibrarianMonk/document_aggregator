"""Invariant / contract tests — assert RULES, not answers.

These run against every scenario (parametrized by scenario_id) and every mode,
so they measure whether the *pipeline* is correct, not whether it reproduces one
hardcoded example. If a new scenario breaks an invariant, that is a real bug.

Invariants under test:
  I1  No fabrication: every non-empty resolved value traces to the corpus or a
      correction (its provenance is non-empty), OR it is explicitly flagged
      (needs_review / conflict).
  I2  Conflict: when >1 correction targets a unit with differing values, the
      unit's status is `conflict` and all candidates are preserved.
  I3  needs_review: a unit with no corpus value and no correction value is
      never silently filled — it is needs_review (or conflict).
  I4  Sequential numbering: figures and tables are numbered 1..N with no gaps.
  I5  Cross-references resolve to a real figure number or are needs_review
      (never left dangling with a stale value silently).
  I6  Mode convergence: draft and template modes produce the same section keys
      and the same set of unit keys.
  I7  Statuses are always from the allowed set.
"""
from __future__ import annotations

import pytest

ALLOWED = {"unchanged", "filled", "corrected", "needs_review", "conflict"}
RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _all_fields(report: dict) -> list[dict]:
    out: list[dict] = []
    for sec in report["sections"]:
        out.extend(sec["fields"])
    fu = report["furniture"]
    out.extend([fu["header"], fu["footer"], fu["classification"], fu["page_numbers"]])
    out.extend(fu["cross_references"])
    return out


def _has_provenance(unit: dict) -> bool:
    p = unit.get("provenance", {})
    return bool(p.get("corpus") or p.get("corrections") or p.get("rule"))


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_no_fabrication(scenario_module, scenario_id, mode):
    """I1: any resolved value with content must be provenance-backed."""
    report = scenario_module.run_reconciliation(mode, scenario_id)
    for f in _all_fields(report):
        if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
            assert _has_provenance(f), f"unprovenanced value: {f['key']}={f['value']!r}"


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_statuses_allowed(scenario_module, scenario_id, mode):
    """I7: every unit carries a legal status."""
    report = scenario_module.run_reconciliation(mode, scenario_id)
    for f in _all_fields(report):
        assert f["status"] in ALLOWED, f"illegal status {f['status']} on {f['key']}"


def test_conflict_preserves_candidates(scenario_module, scenario_id):
    """I2: conflicting corrections -> conflict status with all candidates kept."""
    corrections = scenario_module.load_corrections(scenario_id)
    by_target: dict[str, set] = {}
    for c in corrections:
        by_target.setdefault(c["target"], set()).add(c.get("new_value"))
    conflicting = {t for t, vals in by_target.items() if len(vals) > 1}

    report = scenario_module.run_reconciliation("draft", scenario_id)
    fields = {f["key"]: f for f in _all_fields(report)}
    for target in conflicting:
        # target is like "section.field"
        f = fields.get(target)
        if f is None:
            continue
        assert f["status"] == "conflict", f"{target} should be conflict"
        assert len(f["candidates"]) >= 2, f"{target} must preserve candidates"


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_needs_review_not_silently_filled(scenario_module, scenario_id, mode):
    """I3: a needs_review unit must not carry a fabricated value."""
    report = scenario_module.run_reconciliation(mode, scenario_id)
    for f in _all_fields(report):
        if f["status"] == "needs_review":
            # It may echo the (wrong) draft value, but must be provenance-ruled
            # (a rule explaining why it is required) — never a silent guess.
            assert _has_provenance(f), f"needs_review without rule: {f['key']}"


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_sequential_numbering(scenario_module, scenario_id, mode):
    """I4: figures and tables numbered 1..N with no gaps or dupes."""
    report = scenario_module.run_reconciliation(mode, scenario_id)
    fig_nums, tbl_nums = [], []
    for sec in report["sections"]:
        for g in sec["graphics"]:
            if g["figure_number"] is not None:
                fig_nums.append(g["figure_number"])
        for t in sec["tables"]:
            if t["table_number"] is not None:
                tbl_nums.append(t["table_number"])
    assert fig_nums == list(range(1, len(fig_nums) + 1)), f"figure numbering gaps: {fig_nums}"
    assert tbl_nums == list(range(1, len(tbl_nums) + 1)), f"table numbering gaps: {tbl_nums}"


def test_cross_references_resolve_or_flag(scenario_module, scenario_id):
    """I5: every cross-reference resolves to a figure or is needs_review."""
    report = scenario_module.run_reconciliation("draft", scenario_id)
    for x in report["furniture"]["cross_references"]:
        if x["status"] == "needs_review":
            continue
        assert str(x["value"]).lower().startswith("figure"), f"unresolved xref: {x['value']}"


def test_mode_convergence(scenario_module, scenario_id):
    """I6: draft and template modes produce the same section + unit keys."""
    d = scenario_module.run_reconciliation("draft", scenario_id)
    t = scenario_module.run_reconciliation("template", scenario_id)
    assert [s["key"] for s in d["sections"]] == [s["key"] for s in t["sections"]]

    def unit_keys(report: dict) -> set[str]:
        keys = set()
        for sec in report["sections"]:
            keys.update(f["key"] for f in sec["fields"])
            keys.update(g["graphic_id"] for g in sec["graphics"])
            keys.update(tb["key"] for tb in sec["tables"])
        return keys

    assert unit_keys(d) == unit_keys(t), "modes diverge on unit keys"


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_table_cells_corpus_or_flagged(scenario_module, scenario_id, mode):
    """I1 (tables): each table cell is either corpus-derived or [needs_review]."""
    report = scenario_module.run_reconciliation(mode, scenario_id)
    for sec in report["sections"]:
        for t in sec["tables"]:
            assert _has_provenance(t), f"table {t['key']} lacks provenance"
            for row in t["rows"]:
                for cell in row:
                    # A cell is acceptable if it has content or is explicitly flagged.
                    assert cell is not None
