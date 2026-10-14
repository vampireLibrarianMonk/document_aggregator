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


def test_junk_leak_axis_scores_only_precision() -> None:
    """The junk-leak axis is a pure PRECISION probe: a non-game record whose
    field names share golden tokens. It has NO true golden field, so recall is
    not scored (present_golden == 0 at every severity) and only false_match
    carries signal. severity 0 (empty record) must be clean."""
    fr = run_axis("junk_leak", seed=1337)
    assert all(s.present_golden == 0 for s in fr.scores)
    assert fr.scores[0].false_match == 0          # empty record grounds nothing
    assert fr.break_severity is None              # no required field to break
    # After the value-evidence gate + value-corroboration region veto, the leak
    # is CLOSED: every golden-adjacent junk field is vetoed (the former
    # publisher_region residual is caught by the region-value veto). The 5-leak
    # baseline must not return.
    assert all(s.false_match == 0 for s in fr.scores), (
        f"junk-leak regressed: {[s.false_match for s in fr.scores]}")


def test_junk_control_is_zero_after_value_gate() -> None:
    """The hard-negative junk control (the headline junk_fp number) must be 0
    after the value-evidence gate: a non-game record grounds no golden field."""
    fr = run_axis("rename", seed=1337)   # junk control runs every severity
    assert all(s.junk_false_match == 0 for s in fr.scores), (
        f"junk control leaked: {[s.junk_false_match for s in fr.scores]}")


def test_semantic_booster_is_precision_neutral() -> None:
    """The gated embedding booster re-ranks the candidate LIST only; it must not
    change any grounding decision (mapped/conflict/recovered/false_match) vs the
    booster being off. Measured mandate: embeddings help ordering/recall-surfacing,
    the lexical+value gates do all grounding. We assert ON == OFF on the axes most
    able to expose a precision change (junk_leak, conflict, noise_decoyonly)."""
    import app.json_alignment.mapping as m

    from .harness import run_axis

    if not m._semantic_available():
        pytest.skip("semantic booster inert without a real model")

    def snap() -> dict:
        out = {}
        for ax in ("junk_leak", "conflict", "noise_decoyonly", "rename"):
            fr = run_axis(ax, seed=1337)
            out[ax] = [(s.false_match, s.recovered, s.conflicts) for s in fr.scores]
        return out

    on = snap()
    orig = m._semantic_available
    try:
        m._semantic_available = lambda: False
        off = snap()
    finally:
        m._semantic_available = orig
    assert on == off, f"semantic booster changed a grounding decision: {on} != {off}"


def test_semantic_booster_never_selects_grounded_path_on_exact_tie() -> None:
    """Adversarial coverage (Guardian): two DISTINCT source paths that both score
    'exact'/'normalized' for one target (e.g. two nested '.title' leaves) are NOT
    a harness axis (the axes enforce unique leaves), so this guards the case
    directly: the GROUNDED path must be chosen by the deterministic lexical+path
    order, never by the embedding booster. Only the candidate DISPLAY order may
    differ ON vs OFF."""
    import app.json_alignment.mapping as m
    from app.json_alignment.source_profile import profile_source
    from app.json_alignment.target_profile import extract_target

    if not m._semantic_available():
        pytest.skip("semantic booster inert without a real model")
    schema = {"title": "t", "properties": {
        "title": {"type": "string",
                  "description": "the film title shown in cinemas and on posters"}}}
    src = profile_source([{"z_alpha": {"title": "A"}, "a_beta": {"title": "B"}}])
    tgt = extract_target(schema)

    def grounded() -> tuple:
        fm = next(f for f in m.infer_mapping(src, tgt).fields if f.target == "title")
        return fm.source_path, fm.status

    on = grounded()
    orig = m._semantic_available
    try:
        m._semantic_available = lambda: False
        off = grounded()
    finally:
        m._semantic_available = orig
    assert on == off, (
        f"booster changed the grounded path on an exact tie: {on} != {off}")


def test_evidence_string_builder_is_deterministic() -> None:
    """The evidence-string builder (name + description + example values) is pure
    and order-stable — Task 8's embedding tier will reuse it, so lock its shape."""
    from .evidence import evidence_string

    a = evidence_string("criticScore", "critic review score out of 100", [90, 92])
    b = evidence_string("criticScore", "critic review score out of 100", [90, 92])
    assert a == b
    assert "critic score" in a          # camelCase split
    assert "90" in a and "92" in a      # values included
    assert evidence_string("x") == "x"  # bare name degrades gracefully


def test_evidence_embeddings_are_recall_signal_not_precision() -> None:
    """The HONEST go/no-go measurement for the embedding tier, on an in-domain
    hard-negative fixture (junk shares the golden's VOCABULARY but is a different
    entity — the realistic analytic-report case). Two findings this locks:

      1. RECALL: embedding the EVIDENCE STRING (name+description+values) ranks
         every real synonym's top golden CORRECTLY (7/7), and does so where the
         BARE NAME fails (bare gets several wrong and inverts signal/junk). So
         embeddings ARE a good recall/ranking signal on rich text.
      2. PRECISION: embeddings alone do NOT separate signal from in-domain junk
         — the margin (weakest synonym top minus strongest junk top) is NOT
         positive, because a junk column like 'year_installed' can be as close to
         'releaseYear' as a real synonym. Therefore the embedding tier must be
         RECALL-ONLY and every embedding proposal MUST pass the value-evidence
         gate before it can ground. This is the measured mandate for Task 8's
         design (gated booster, never authoritative on cosine alone).

    Skipped only when the real model is unavailable (hashing carries no meaning)."""
    from app.embeddings import embedder

    from .evidence import measure_separation

    if "minilm" not in embedder.model.lower():
        pytest.skip(f"semantic separation needs a real model; active={embedder.model}")
    out = measure_separation()
    ev = out["evidence_string"]
    bare = out["bare_name"]
    # Finding 1 — recall: evidence strings rank every synonym correctly; bare does not.
    assert ev["synonyms_top_correct"] == ev["synonyms_total"], (
        f"evidence strings mis-ranked a synonym: {ev['synonyms_top_correct']}"
        f"/{ev['synonyms_total']}")
    assert bare["synonyms_top_correct"] < bare["synonyms_total"], (
        "bare names unexpectedly ranked every synonym correctly — fixture may be "
        "too easy; the whole point is that rich evidence beats bare names")
    # Finding 2 — precision: embeddings alone do NOT separate in-domain junk, so
    # the tier must be value-gated, not cosine-authoritative. (If this ever flips
    # positive it means the junk got too easy; keep it honest.)
    assert ev["margin"] <= 0.0, (
        f"evidence margin positive ({ev['margin']}) — the in-domain junk fixture "
        f"has gone soft; embeddings must NOT look like a standalone precision cut")


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
