"""Tests for the relevance gate (Phase A): is this JSON even related to the
golden schema, and does it clear the per-project reject dial?

Locks the user-approved policy: zero mappable required fields is ALWAYS a reject
(the hard floor); otherwise required-field coverage is compared to reject_below
(a dial), with a review band just above the line. Precision-first: an unrelated
file abstains on everything -> coverage 0 -> reject, never a fabricated match.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.mapping import infer_mapping
from app.json_alignment.relevance import (
    DEFAULT_REJECT_BELOW,
    VERDICT_CONVERTIBLE,
    VERDICT_REJECT,
    VERDICT_REVIEW,
    assess_relevance,
    relevance_of,
)
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target
from app.json_alignment.variation import generate_variations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _vars() -> dict:
    golden = json.loads((VAR_DIR / "golden_records.json").read_text())
    return {v.name: v for v in generate_variations(golden, _schema(), seed=42)}


def test_default_dial_is_half():
    assert DEFAULT_REJECT_BELOW == 0.5


def test_unrelated_json_is_rejected():
    v = relevance_of([{"weather": "sunny", "temp_c": 21, "wind": 5}], _schema())
    assert v.verdict == VERDICT_REJECT
    assert v.required_coverage == 0.0
    assert v.required_mapped == 0
    assert "zero required golden fields could be grounded" in " ".join(v.reasons)


def test_fully_grounded_is_convertible():
    op = _vars()["level_09"]  # opaque names + descriptions -> full recovery
    v = relevance_of(op.records, _schema(), source_descriptions=op.descriptions)
    assert v.verdict == VERDICT_CONVERTIBLE
    assert v.required_coverage == 1.0


def test_zero_required_is_hard_reject_even_with_permissive_dial():
    # Dial at 0 would normally pass anything, but the hard floor still fires
    # when NOTHING of the required core maps.
    v = relevance_of([{"wholly": "unrelated", "nothing": "matches"}], _schema(),
                     reject_below=0.0)
    assert v.verdict == VERDICT_REJECT


def test_partial_required_coverage_lands_in_review_band():
    # 'name' maps, 'releaseYear' does not -> 50% required coverage. With a dial
    # of 0.4 that clears the line but sits inside the review margin.
    recs = [{"name": "X", "foo": 1, "bar": 2}]
    v = relevance_of(recs, _schema(), reject_below=0.4)
    assert v.verdict == VERDICT_REVIEW
    assert v.required_coverage == 0.5
    assert v.unmapped_required == ("releaseYear",)
    assert v.needs_human


def test_dial_controls_the_reject_line():
    recs = [{"name": "X", "foo": 1}]  # 50% required coverage
    # strict dial rejects it...
    assert relevance_of(recs, _schema(), reject_below=0.75).verdict == VERDICT_REJECT
    # ...permissive dial accepts it (0.5 >= 0.0 + margin 0.2)
    assert relevance_of(recs, _schema(), reject_below=0.0).verdict == VERDICT_CONVERTIBLE


def test_dial_is_clamped_to_unit_interval():
    recs = [{"name": "X"}]
    # out-of-range dials must not crash or misbehave
    assert relevance_of(recs, _schema(), reject_below=5.0).verdict in {
        VERDICT_REJECT, VERDICT_REVIEW, VERDICT_CONVERTIBLE}
    assert relevance_of(recs, _schema(), reject_below=-1.0).reject_below == 0.0


def test_schema_with_no_required_fields_uses_overall_coverage():
    schema = {"title": "t", "properties": {
        "a": {"type": "string", "description": "alpha"},
        "b": {"type": "string", "description": "bravo"}}}  # no 'required'
    # a file that maps nothing -> reject on the overall-coverage floor
    rej = relevance_of([{"zzz": 1}], schema)
    assert rej.verdict == VERDICT_REJECT
    # a file that maps both -> convertible
    ok = relevance_of([{"a": "x", "b": "y"}], schema)
    assert ok.verdict == VERDICT_CONVERTIBLE


def test_assess_relevance_is_deterministic():
    recs = [{"name": "X", "foo": 1}]
    src = profile_source(recs)
    target = extract_target(_schema())
    mapping = infer_mapping(src, target)
    a = assess_relevance(mapping, target, reject_below=0.4)
    b = assess_relevance(mapping, target, reject_below=0.4)
    assert a.to_dict() == b.to_dict()
