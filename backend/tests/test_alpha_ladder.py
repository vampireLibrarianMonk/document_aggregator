"""Opt-in regression guard for the FULL adversarial ladder (all 12 stages).

The alpha-loop benign->hostile ladder was proven interactively (live stack).
This locks it in as a committed, DESELECTED-BY-DEFAULT pytest so a matcher
change can't silently regress the progressive JSON-workflow behavior.

Modular by design:
  - the stages come from the ONE source of truth, alpha_loop.generator.STAGES
    (add a stage there and it is automatically exercised here);
  - the expected per-stage outcome is a DECLARATIVE table (_EXPECTED below),
    one row per stage — the only thing to touch when behavior intentionally
    changes;
  - the universal invariant (zero false matches) is asserted for EVERY stage
    independent of the table, so precision can never regress unnoticed.

Run with the other opt-in gates:  pytest -m opt_in
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from alpha_loop.generator import STAGES, Generator  # noqa: E402
from app.json_alignment.pipeline import run_alignment  # noqa: E402

pytestmark = pytest.mark.opt_in

_SCHEMA = json.loads(
    (BACKEND.parent / "sample_docs" / "project" / "7" / "golden"
     / "target_schema.json").read_text())


# -- Declarative expectations (the only thing to edit when behavior changes) --
# Per stage: (min recovered-of-present, exact needs_review, exact conflicts).
# `recovered` is a FLOOR (a precision/recall improvement must not fail the test);
# needs_review/conflicts are the recorded abstain/defer shape. false_match is
# asserted == 0 for ALL stages separately (the load-bearing invariant).
_EXPECTED: dict[str, tuple[int, int, int]] = {
    "benign_clean":          (8, 0, 0),
    "benign_rename_shared":  (7, 1, 0),
    "rename_synonym":        (7, 1, 0),
    "nesting":               (7, 1, 0),
    "misspell":              (0, 8, 0),   # keys too corrupted: safe refusal
    "typecoerce":            (8, 0, 0),
    "noise_decoys":          (8, 0, 0),   # decoys correctly unmapped
    "rename_abbrev":         (1, 7, 0),   # abbreviations: abstain, not guess
    "conflict":              (7, 0, 1),   # name decoys tie -> conflict
    "hostile_opaque":        (0, 8, 0),   # no signal: safe refusal
    "hostile_split_merge":   (5, 3, 0),   # split name out of 1:1 scope
    "hostile_junk_leak":     (0, 8, 0),   # golden-adjacent junk: zero grounded
}


def _score(doc):
    """Run the matcher on a stage doc and return (recovered, false, nr, conf,
    present) scored against the generator's carried ground truth."""
    r = run_alignment(doc.records, _SCHEMA,
                      source_descriptions=doc.descriptions or None)
    mapped = r.mapping.mapped()
    truth_inv: dict[str, set[str]] = {}
    for k, g in doc.truth.items():
        if g is not None:
            truth_inv.setdefault(g, set()).add(k.split(".")[-1].replace("[]", ""))
    present = {g for g in doc.truth.values() if g is not None}
    recovered = sum(
        1 for g in present
        if g in mapped and mapped[g].split(".")[-1].replace("[]", "") in truth_inv.get(g, set()))
    false = sum(
        1 for g, s in mapped.items()
        if g not in present or s.split(".")[-1].replace("[]", "") not in truth_inv.get(g, set()))
    return recovered, false, len(r.mapping.needs_review()), len(r.mapping.conflicts()), len(present)


@pytest.mark.parametrize("idx,stage", list(enumerate(name for name, _ in STAGES)))
def test_ladder_stage_zero_false_matches(idx, stage):
    """UNIVERSAL INVARIANT: no fabricated correspondence at ANY stage."""
    doc = Generator().emit_stage(idx, n_per_shape=3, round_id=1)[0]
    _rec, false, _nr, _conf, _present = _score(doc)
    assert false == 0, f"stage '{stage}' produced {false} false match(es) — fabrication"


@pytest.mark.parametrize("idx,stage", list(enumerate(name for name, _ in STAGES)))
def test_ladder_stage_expected_shape(idx, stage):
    """Each stage's recover/abstain/defer shape matches the recorded expectation
    (recovered is a floor; needs_review/conflicts exact)."""
    if stage not in _EXPECTED:
        pytest.fail(f"stage '{stage}' has no _EXPECTED row — add one (modular table)")
    min_rec, exp_nr, exp_conf = _EXPECTED[stage]
    doc = Generator().emit_stage(idx, n_per_shape=3, round_id=1)[0]
    rec, false, nr, conf, present = _score(doc)
    assert rec >= min_rec, (
        f"stage '{stage}' recall regressed: recovered {rec}/{present}, "
        f"expected >= {min_rec}")
    assert nr == exp_nr, f"stage '{stage}' needs_review {nr} != expected {exp_nr}"
    assert conf == exp_conf, f"stage '{stage}' conflicts {conf} != expected {exp_conf}"


def test_ladder_table_covers_every_stage():
    """Modularity guard: the expected table and the generator's STAGES stay in
    sync — a new stage must get an expectation row (or this fails loudly)."""
    stage_names = {name for name, _ in STAGES}
    missing = stage_names - set(_EXPECTED)
    extra = set(_EXPECTED) - stage_names
    assert not missing, f"stages with no _EXPECTED row: {sorted(missing)}"
    assert not extra, f"_EXPECTED rows for non-existent stages: {sorted(extra)}"
