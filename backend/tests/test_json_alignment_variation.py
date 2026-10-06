"""Tests for the deterministic 1:1 variation generator + the tuning exemplar.

These exercise the "many variations -> one golden" workload directly: generate
source variations of a golden record set under escalating difficulty knobs, then
score the deterministic matcher. The load-bearing invariant is PRECISION: across
every difficulty level the matcher must never emit a wrong correspondence
(zero false positives = no fabrication). Recall varies with how much signal a
rename leaves behind and is informational, not asserted tightly.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.source_profile import profile_source
from app.json_alignment.variation import (
    DIFFICULTY_LEVELS,
    generate_variation,
    generate_variations,
    score_variation,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _golden() -> list[dict]:
    return json.loads((VAR_DIR / "golden_records.json").read_text())


def test_generate_is_deterministic_per_seed():
    schema, golden = _schema(), _golden()
    a = generate_variations(golden, schema, seed=7)
    b = generate_variations(golden, schema, seed=7)
    assert [v.to_dict() for v in a] == [v.to_dict() for v in b]
    # a different seed changes the synonym choices
    c = generate_variations(golden, schema, seed=8)
    assert [v.records for v in a] != [v.records for v in c]


def test_gold_paths_match_what_the_profiler_emits():
    """Every gold source path must actually appear in the generated records, and
    only the intentional irrelevant fields may be present without a gold entry."""
    schema, golden = _schema(), _golden()
    for v in generate_variations(golden, schema, seed=42):
        profiled = {f.path for f in profile_source(v.records).fields}
        gold_paths = set(v.gold_mapping)
        assert gold_paths <= profiled, f"{v.name}: gold path absent from records"
        extra = profiled - gold_paths
        assert extra <= {"internal_id", "_scraped_at"}, \
            f"{v.name}: unexpected non-gold path {extra}"


def test_variation_shape_is_consistent_across_records():
    """One variation = one team's export: all records share the same key shape."""
    schema, golden = _schema(), _golden()
    for v in generate_variations(golden, schema, seed=42):
        if len(v.records) < 2:
            continue
        shapes = [sorted({f.path for f in profile_source([r]).fields}
                         - {"internal_id", "_scraped_at"}) for r in v.records]
        # every record profiles to the same (minus-irrelevant) path set
        assert all(s == shapes[0] for s in shapes), f"{v.name}: inconsistent shape"


def test_precision_is_perfect_across_all_levels_no_fabrication():
    """The core guarantee: zero false positives at every difficulty level."""
    schema, golden = _schema(), _golden()
    for v in generate_variations(golden, schema, seed=42):
        score = score_variation(v, schema)
        assert score.false_positives == 0, (
            f"{v.name} fabricated: {score.fp_pairs}")
        assert score.precision == 1.0, f"{v.name} precision {score.precision}"


def test_opaque_with_descriptions_recovers_full_recall():
    """When a source ships field descriptions (the opaque knob's sidecar), the
    matcher recovers the full mapping deterministically."""
    schema, golden = _schema(), _golden()
    opaque = generate_variation(
        golden, list(schema["properties"]), schema.get("required", []),
        ("opaque",), name="opaque", level=9, seed=1)
    score = score_variation(opaque, schema)
    assert score.recall == 1.0 and score.false_positives == 0


def test_pure_rename_without_signal_is_honestly_hard():
    """A blind rename with no description leaves little deterministic signal, so
    recall is low BUT precision stays perfect (we abstain, never guess wrong).
    This is exactly what motivates learn-once-per-cluster + human approval."""
    schema, golden = _schema(), _golden()
    rename = generate_variation(
        golden, list(schema["properties"]), schema.get("required", []),
        ("rename",), name="rename", level=1, seed=3)
    score = score_variation(rename, schema)
    assert score.false_positives == 0
    assert score.recall < 1.0  # cannot fully recover blind renames deterministically


def test_difficulty_levels_cover_the_1to1_ladder():
    # split/merge (level 5) is intentionally absent (1:1 scope).
    assert 5 not in DIFFICULTY_LEVELS
    assert set(DIFFICULTY_LEVELS) == {1, 2, 3, 4, 6, 7, 9, 10}
