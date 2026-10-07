"""Tests for drift detection (Phase B): when must a cluster leave the fast
deterministic-replay path and re-emerge into research?

Covers all three drift kinds and localization to broken fields (so re-research
targets only what changed), plus the no-drift happy path and the fill-rate
baseline. Drift is always a flag, never a silent wrong conversion.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.drift import (
    DRIFT_BEHAVIORAL,
    DRIFT_STRUCTURAL,
    DRIFT_TYPE,
    FillBaseline,
    assess_drift,
    fill_rates,
)
from app.json_alignment.executor import execute_mapping
from app.json_alignment.mapping import infer_mapping
from app.json_alignment.provenance import ValueProvenance
from app.json_alignment.signature import signature_of
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


def _align(records, descs=None):
    src = profile_source(records, descs)
    tgt = extract_target(_schema())
    m = infer_mapping(src, tgt)
    _, prov = execute_mapping(records, m, tgt)
    return src, m, prov


# -- fill_rates / baseline ---------------------------------------------------

def test_fill_rates_from_provenance():
    prov = [
        [ValueProvenance("a", "filled"), ValueProvenance("b", "needs_review")],
        [ValueProvenance("a", "filled"), ValueProvenance("b", "filled")],
    ]
    rates = fill_rates(prov)
    assert rates["a"] == 1.0 and rates["b"] == 0.5


def test_fill_rates_empty():
    assert fill_rates([]) == {}


def test_baseline_roundtrip():
    b = FillBaseline(rates={"a": 1.0, "b": 0.5}, sample_size=2)
    back = FillBaseline.from_dict(b.to_dict())
    assert back.rates == b.rates and back.sample_size == 2


# -- no drift ----------------------------------------------------------------

def test_no_drift_when_shape_and_fill_match():
    op = _vars()["level_09"]
    _, _, prov = _align(op.records, op.descriptions)
    base = FillBaseline.from_provenance(prov)
    sig = signature_of(op.records)
    r = assess_drift(sig, sig, current_fill=fill_rates(prov), baseline=base)
    assert not r.drifted
    assert r.kinds == ()
    assert r.broken_field_names == ()


# -- behavioral drift --------------------------------------------------------

def test_behavioral_drift_when_fill_rate_collapses():
    op = _vars()["level_09"]
    _, _, base_prov = _align(op.records, op.descriptions)  # all filled
    baseline = FillBaseline.from_provenance(base_prov)
    base_sig = signature_of(op.records)

    # Same opaque shape, but WITHOUT descriptions -> matcher abstains -> fills drop
    _, _, now_prov = _align(op.records, None)
    r = assess_drift(base_sig, base_sig,
                     current_fill=fill_rates(now_prov), baseline=baseline)
    assert r.drifted
    assert DRIFT_BEHAVIORAL in r.kinds
    # structural/type did NOT fire: identical shape + types
    assert DRIFT_STRUCTURAL not in r.kinds
    assert DRIFT_TYPE not in r.kinds
    assert "name" in r.broken_field_names


def test_behavioral_drop_below_tolerance_is_not_drift():
    base = FillBaseline(rates={"a": 1.0}, sample_size=10)
    # a 10% drop is under the default 25% tolerance
    r = assess_drift(signature_of([{"a": 1}]), signature_of([{"a": 1}]),
                     current_fill={"a": 0.9}, baseline=base)
    assert not r.drifted


# -- structural drift --------------------------------------------------------

def test_structural_drift_on_added_and_removed_fields():
    op = _vars()["level_09"]
    base_sig = signature_of(op.records)
    mutated = [dict(r) for r in op.records]
    for rec in mutated:
        rec["brand_new_field"] = "x"
        rec.pop("f08", None)
    r = assess_drift(signature_of(mutated), base_sig)
    assert r.drifted and DRIFT_STRUCTURAL in r.kinds
    assert r.signature_match is not None
    assert any("brand" in n for n in r.broken_field_names)


# -- type drift --------------------------------------------------------------

def test_type_drift_localizes_to_the_changed_field():
    op = _vars()["level_09"]
    base_sig = signature_of(op.records)
    typed = [dict(r) for r in op.records]
    for rec in typed:
        rec["f07"] = str(rec["f07"])  # int -> string
    r = assess_drift(signature_of(typed), base_sig)
    assert r.drifted and DRIFT_TYPE in r.kinds
    # the changed source field is the only field flagged for type drift
    type_fields = [fd.field for fd in r.broken_fields if DRIFT_TYPE in fd.kinds]
    assert type_fields == ["f07"]


def test_mapping_targets_relabel_broken_fields_to_target_names():
    # When a source->target map is supplied, a structural change on a mapped
    # source path is reported against the TARGET field it feeds.
    base = signature_of([{"old_key": "v", "stable": 1}])
    now = signature_of([{"stable": 1}])  # old_key vanished
    r = assess_drift(now, base, mapping_targets={"old key": "importantTarget"})
    # normalized path 'old key' -> target 'importantTarget'
    assert "importantTarget" in r.broken_field_names
