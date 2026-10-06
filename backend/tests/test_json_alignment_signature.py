"""Tests for source-shape signatures + library matching (Part 2 of batch align).

Signatures are value-independent fingerprints used to route an incoming
document to the right learned profile. These lock the properties the batch
coordinator will depend on: same shape -> same signature; cosmetic key styling
doesn't fracture a shape; a type change downgrades an otherwise-high match so it
never silently rides the fast replay path; a brand-new shape routes to novel.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.signature import (
    BAND_HIGH,
    BAND_MODERATE,
    BAND_NONE,
    Signature,
    best_match,
    compare,
    signature_of,
    source_signature,
)
from app.json_alignment.source_profile import profile_source
from app.json_alignment.variation import generate_variations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


def _vars() -> dict:
    schema = json.loads((FIXTURE_DIR / "target_schema.json").read_text())
    golden = json.loads((VAR_DIR / "golden_records.json").read_text())
    return {v.name: v for v in generate_variations(golden, schema, seed=42)}


def test_same_shape_same_signature_regardless_of_values():
    recs = [{"name": "A", "year": 2001}, {"name": "B", "year": 2007}]
    other_values = [{"name": "ZZZ", "year": 1999}]
    assert signature_of(recs).hash == signature_of(other_values).hash


def test_cosmetic_key_styling_does_not_fracture_the_shape():
    # camelCase vs snake_case vs kebab of the same keys -> same normalized paths
    a = signature_of([{"gameTitle": "x", "release_year": 2001}])
    b = signature_of([{"game_title": "x", "release-year": 2001}])
    assert a.paths == b.paths


def test_bookkeeping_fields_are_ignored():
    base = signature_of([{"name": "x"}])
    noisy = signature_of([{"name": "x", "internal_id": "r1", "_scraped_at": "t"}])
    assert base.hash == noisy.hash


def test_type_drift_downgrades_high_to_moderate():
    ref = signature_of([{"name": "x", "score": 90}])
    drifted = signature_of([{"name": "x", "score": "90"}])  # int -> string
    m = compare(drifted, ref)
    assert m.score == 1.0            # identical path set
    assert m.band == BAND_MODERATE   # but a type changed -> not HIGH
    assert "score" in m.type_mismatches


def test_identical_signature_is_high_band():
    ref = signature_of([{"name": "x", "score": 90}])
    same = signature_of([{"name": "y", "score": 50}])
    assert compare(same, ref).band == BAND_HIGH


def test_different_shapes_are_none_band():
    flat = _vars()["level_01"]
    nested = _vars()["level_03"]
    m = compare(signature_of(flat.records), signature_of(nested.records))
    assert m.band == BAND_NONE


def test_best_match_routes_known_shape_and_flags_novel():
    vars = _vars()
    refs = {name: signature_of(v.records) for name, v in vars.items()}
    # a known shape routes back to its own profile at HIGH
    pid, match = best_match(signature_of(vars["level_01"].records), refs)
    assert pid == "level_01" and match.band == BAND_HIGH
    # a brand-new shape is novel
    novel = [{"completely": "different", "unseen": 1, "foo": {"bar": 2}}]
    pid2, m2 = best_match(signature_of(novel), refs)
    assert pid2 is None and m2.band == BAND_NONE


def test_signature_roundtrip():
    sig = signature_of([{"name": "x", "score": 90}])
    back = Signature.from_dict(sig.to_dict())
    assert back.paths == sig.paths and back.types == sig.types and back.hash == sig.hash


def test_source_signature_from_profile_matches_convenience():
    recs = [{"name": "x", "score": 90}]
    assert source_signature(profile_source(recs)).hash == signature_of(recs).hash
