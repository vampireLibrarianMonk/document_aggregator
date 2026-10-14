"""Light smoke test for the conformance-frontier alpha loop.

This is NOT the research sweep (that is run on demand via
`python -m tests.alpha_frontier.harness`). It is a fast guard that the harness
stays wired to the real matcher, that its invariants hold, and — most
importantly — that the adversarial semantic-tier exercise still proves the
re-verification gate's contract. If any of these regress, the frontier results
can no longer be trusted, so they are asserted here.
"""
from __future__ import annotations

import random

import pytest

from .harness import (
    _assert_leaf_unique,
    _bare,
    _score_one,
    run_axis,
    run_semantic_tier_exercise,
)
from .mutations import AXES, REQUIRED_FIELDS


def test_bare_leaf_normalization() -> None:
    assert _bare("level0.name") == "name"
    assert _bare("genres[]") == "genres"
    assert _bare("name") == "name"


def test_axes_emit_unique_leaf_keys() -> None:
    """Every axis at every severity must emit a truth map with unique leaf keys
    (the harness's leaf-based scoring depends on it)."""
    rng = random.Random(0)
    for name, ax in AXES.items():
        for sev in range(ax.max_severity + 1):
            mr = ax.fn(_clean(), sev, rng)
            _assert_leaf_unique(mr)  # raises on collision


def test_score_one_counts_false_match_for_cross_wired_mapping() -> None:
    """A golden mapped to a DIFFERENT golden's key (not a labelled decoy) is a
    false match — the tightened precision rule."""
    # truth: key 'a' is golden X, key 'b' is golden Y.
    from .mutations import MutationResult

    mr = MutationResult({"a": 1, "b": 2}, {"a": "name", "b": "releaseYear"})
    # engine cross-wires: name <- b (which truly is releaseYear's key).
    mapped = {"name": "b", "releaseYear": "b"}
    rec, miss, false, _rr, _rp, _nc = _score_one(mr, mapped, [])
    assert rec == 1          # releaseYear <- b is correct
    assert false == 1        # name <- b is a false match (b is not name's key)


def test_score_one_absent_golden_mapped_is_false_match() -> None:
    """A golden with NO true source key that the engine still maps is a false
    match regardless of what it mapped to."""
    from .mutations import MutationResult

    mr = MutationResult({"x": 1}, {"x": None})   # x is a decoy; no true goldens
    mapped = {"name": "x"}                         # engine grabbed the decoy
    _rec, _miss, false, _rr, _rp, _nc = _score_one(mr, mapped, [])
    assert false == 1


@pytest.mark.parametrize("axis_name", list(AXES))
def test_run_axis_is_monotonic_and_deterministic(axis_name: str) -> None:
    """Each axis runs, is reported monotonic (required-breakage never recovers),
    and is deterministic across two identical runs."""
    a = run_axis(axis_name, seed=1337)
    b = run_axis(axis_name, seed=1337)
    assert a.to_dict() == b.to_dict(), f"{axis_name} not deterministic"
    assert a.monotonic, (
        f"{axis_name} non-monotonic: broken at {a.broken_severities}; the single "
        f"break point would be a mislabel")


def test_junk_control_is_recorded() -> None:
    """The junk/hard-negative control runs and is recorded at every severity (we
    don't assert it's zero — a non-zero is a real finding the sweep surfaces —
    only that the control executed and produced an int)."""
    fr = run_axis("rename", seed=1337)
    for sc in fr.scores:
        assert isinstance(sc.junk_false_match, int)


def test_required_fields_present_in_break_criterion() -> None:
    assert REQUIRED_FIELDS  # non-empty, else nothing could ever "break"


def test_semantic_tier_gate_contract() -> None:
    """The adversarial semantic-tier exercise is the precision crux: it must
    show (1) a fabricator's picks are ALL rejected and add no false match,
    (2) an honest proposer given no real option abstains, and (3) an accepted
    wrong pick is ALWAYS caught by scoring (delta == accepted upgrades). The
    exercise itself raises on any violation; here we re-assert the shape so a
    silent weakening is caught in CI."""
    cases = run_semantic_tier_exercise()
    by_name = {c["proposer"]: c for c in cases}

    fab = by_name["adversarial-fabricator"]
    assert fab["accepted_upgrades"] == []
    assert fab["false_match_delta"] == 0

    honest = by_name["honest(abstains)"]
    assert honest["accepted_upgrades"] == []
    assert honest["false_match_delta"] == 0

    decoy = by_name["adversarial-decoy-picker"]
    # The gate genuinely accepts real+type-compatible wrong picks; scoring must
    # catch every one. This is the "necessary but not sufficient" proof.
    assert decoy["accepted_upgrades"], (
        "decoy-picker accepted nothing — the gate exercise is vacuous; the "
        "scenario must offer a real, type-compatible decoy")
    assert decoy["false_match_delta"] == len(decoy["accepted_upgrades"])

    outset = by_name["adversarial-out-of-set"]
    # The closed-set gate must reject a real, type-compatible pick that is
    # OUTSIDE the target's offered candidate set. Non-vacuous: there must have
    # been something to reject.
    assert outset["rejected_picks"], (
        "out-of-set exercise is vacuous — no out-of-set pick was offered to "
        "the gate (need a conflicted target with a proper-subset candidate set)")
    assert outset["accepted_upgrades"] == []
    assert outset["false_match_delta"] == 0


def _clean() -> dict:
    return {
        "name": "Portal", "releaseYear": "2007", "developer": "Valve",
        "publisher": "Valve", "platform": "Windows PC", "genres": ["Puzzle"],
        "criticScore": 90, "ESRB": "T",
    }
