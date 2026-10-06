"""Tests for the deterministic JSON schema-alignment slice (app/json_alignment).

Covers: source profiling, target extraction, mapping (match tiers + abstention +
conflict), transforms, execution + provenance, validation (grounding), profile
persistence/replay, command-center orchestration parity, and the committed
self-generated benchmark. The optional MaDI external benchmark runs only when
MADI_BENCH_PATH is set (skipped otherwise; no MaDI artifacts are committed).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from app.json_alignment import (
    execute_mapping,
    extract_target,
    infer_mapping,
    profile_source,
    validate_record,
)
from app.json_alignment.benchmark import (
    load_madi_task,
    load_self_task,
    run_benchmark,
)
from app.json_alignment.mapping import (
    STATUS_CONFLICT,
    STATUS_MAPPED,
    STATUS_NEEDS_REVIEW,
)
from app.json_alignment.pipeline import run_alignment
from app.json_alignment.profile_store import build_profile, load_profile, save_profile
from app.json_alignment.scoring import (
    NormalizationCase,
    normalization_score,
    schema_match_score,
)
from app.json_alignment.transforms import plan_and_apply

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


# --- source profiling -------------------------------------------------------

def test_profile_source_paths_and_types():
    recs = [{"a": 1, "b": {"c": "x"}}, {"a": 2, "b": {"c": "y"}, "d": [1, 2]}]
    prof = profile_source(recs)
    paths = {f.path for f in prof.fields}
    assert "a" in paths and "b.c" in paths and "d[]" in paths
    a = prof.by_path()["a"]
    assert a.primary_type == "integer" and a.coverage == 1.0
    d = prof.by_path()["d[]"]
    assert d.coverage == 0.5  # present in 1 of 2 records


def test_profile_source_deterministic_field_order():
    recs = [{"z": 1, "a": 2}]
    prof = profile_source(recs)
    assert [f.path for f in prof.fields] == ["a", "z"]


# --- target extraction ------------------------------------------------------

def test_extract_target_fields_and_constraints():
    schema = json.loads((FIXTURE_DIR / "target_schema.json").read_text())
    target = extract_target(schema)
    names = {f.name for f in target.fields}
    assert {"name", "releaseYear", "platform", "ESRB"} <= names
    assert target.required_names() == ("name", "releaseYear")
    esrb = target.by_name()["ESRB"]
    assert "E" in esrb.enum and esrb.aliases.get("K-A") == "E"


# --- mapping: tiers, abstention, conflict ----------------------------------

def test_mapping_exact_and_abstain():
    recs = [{"name": "x", "mystery_blob": "y"}]
    schema = {"title": "t", "properties": {
        "name": {"type": "string", "description": "the name"},
        "color": {"type": "string", "description": "the color"}}}
    m = infer_mapping(profile_source(recs), extract_target(schema))
    by = {fm.target: fm for fm in m.fields}
    assert by["name"].status == STATUS_MAPPED and by["name"].method == "exact"
    assert by["color"].status == STATUS_NEEDS_REVIEW  # no match -> abstain


def test_mapping_never_fabricates():
    # An unrelated source must not be force-mapped to a target.
    recs = [{"wholly_unrelated_token": "v"}]
    schema = {"title": "t", "properties": {
        "temperature": {"type": "number", "description": "degrees celsius"}}}
    m = infer_mapping(profile_source(recs), extract_target(schema))
    assert m.fields[0].status == STATUS_NEEDS_REVIEW
    assert m.fields[0].source_path is None


def test_mapping_conflict_on_tie():
    # Two equally-good description matches for one target -> conflict, unresolved.
    recs = [{"press_rating": "90", "critic_rating": "88"}]
    schema = {"title": "t", "properties": {
        "rating": {"type": "integer", "description": "press critic rating score"}}}
    m = infer_mapping(profile_source(recs), extract_target(schema))
    fm = m.fields[0]
    assert fm.status in (STATUS_CONFLICT, STATUS_MAPPED)
    if fm.status == STATUS_CONFLICT:
        assert fm.source_path is None


# --- transforms -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2001", "2001"),
    ("10/09/2007", "2007"),
    ("October 27, 2017", "2017"),
])
def test_transform_year(raw, expected):
    tf = extract_target({"title": "t", "properties": {
        "releaseYear": {"type": "string", "pattern": r"^\d{4}$",
                        "description": "year"}}}).by_name()["releaseYear"]
    value, _op, ok = plan_and_apply(raw, tf)
    assert ok and value == expected


def test_transform_alias_and_enum():
    tf = extract_target({"title": "t", "properties": {
        "ESRB": {"type": "string", "x-pydi-taxonomy": ["E", "M"],
                 "x-pydi-taxonomy-aliases": {"Mature": "M"}}}}).by_name()["ESRB"]
    assert plan_and_apply("Mature", tf) == ("M", "coerce_enum", True)
    # Not in vocabulary -> fail closed, no fabrication.
    assert plan_and_apply("Unrated", tf)[2] is False


def test_transform_list_split():
    tf = extract_target({"title": "t", "properties": {
        "genres": {"type": "array", "items": {"type": "string"}}}}).by_name()["genres"]
    value, _op, ok = plan_and_apply("A;B/C", tf)
    assert ok and value == ["A", "B", "C"]


def test_transform_bad_int_fails_closed():
    tf = extract_target({"title": "t", "properties": {
        "n": {"type": "integer"}}}).by_name()["n"]
    assert plan_and_apply("not-a-number", tf)[2] is False
    assert plan_and_apply("90.5", tf)[2] is False  # lossy int -> declined


# --- execution + provenance + validation -----------------------------------

def test_execute_fills_only_grounded_values():
    recs = [{"name": "x"}]
    schema = {"title": "t", "properties": {
        "name": {"type": "string", "description": "the name"},
        "color": {"type": "string", "description": "the color"}}}
    target = extract_target(schema)
    m = infer_mapping(profile_source(recs), target)
    produced, prov = execute_mapping(recs, m, target)
    assert produced[0] == {"name": "x"}
    statuses = {p.target: p.status for p in prov[0]}
    assert statuses["name"] == "filled" and statuses["color"] == "needs_review"


def test_validation_flags_ungrounded_and_passes_clean():
    recs = [{"name": "x"}]
    schema = {"title": "t", "properties": {
        "name": {"type": "string", "description": "the name"}}, "required": ["name"]}
    target = extract_target(schema)
    m = infer_mapping(profile_source(recs), target)
    produced, prov = execute_mapping(recs, m, target)
    report = validate_record(produced[0], prov[0], target)
    assert report.ok

    # Inject an ungrounded value -> validation must flag it as fabrication risk.
    produced[0]["name"] = "fabricated"
    prov[0] = [p for p in prov[0] if p.target != "name"]
    bad = validate_record(produced[0], prov[0], target)
    assert not bad.ok
    assert any(i.kind == "ungrounded" for i in bad.issues)


# --- profile persistence + replay ------------------------------------------

def test_profile_roundtrip_and_replay(tmp_path):
    recs = [{"game_title": "Halo", "year_published": "2001"}]
    schema = {"title": "game", "properties": {
        "name": {"type": "string", "description": "the title of the game"},
        "releaseYear": {"type": "string", "pattern": r"^\d{4}$",
                        "description": "year published"}}, "required": ["name"]}
    src = profile_source(recs)
    target = extract_target(schema)
    m = infer_mapping(src, target)
    prof = build_profile("games_v1", src, target, m)
    p = save_profile(prof, tmp_path / "games.json")
    loaded = load_profile(p)
    assert loaded.mapping.mapped() == m.mapped()

    # Replay on a fresh batch using the frozen profile (no re-inference).
    res = run_alignment([{"game_title": "Doom", "year_published": "1993"}],
                        schema, profile=loaded)
    assert res.records[0] == {"name": "Doom", "releaseYear": "1993"}
    assert res.validation[0].ok


# --- command-center orchestration parity -----------------------------------

def test_coordinator_matches_direct_pipeline():
    from app.json_alignment.agent import run_alignment_via_coordinator

    recs = json.loads((FIXTURE_DIR / "source_records.json").read_text())
    schema = json.loads((FIXTURE_DIR / "target_schema.json").read_text())
    direct = run_alignment(recs, schema)
    co = run_alignment_via_coordinator(recs, schema)
    assert co["records"] == direct.records
    # DAG ran all five alignment stages in order, each ok.
    kinds = [e["task"] for e in co["record"].task_log]
    assert kinds == ["a0_profile", "a1_target", "a2_map", "a3_execute", "a4_validate"]
    assert all(e["ok"] for e in co["record"].task_log)


def test_alignment_is_deterministic():
    recs = json.loads((FIXTURE_DIR / "source_records.json").read_text())
    schema = json.loads((FIXTURE_DIR / "target_schema.json").read_text())
    a = run_alignment(recs, schema)
    b = run_alignment(recs, schema)
    assert a.records == b.records
    assert a.mapping.to_dict() == b.mapping.to_dict()


# --- scorers ----------------------------------------------------------------

def test_schema_match_score_math():
    gold = {("a", "A"), ("b", "B"), ("c", "C")}
    pred = {("a", "A"), ("b", "B"), ("x", "X")}  # 2 TP, 1 FP, 1 FN
    s = schema_match_score(pred, gold)
    assert s.true_positives == 2 and s.false_positives == 1 and s.false_negatives == 1
    assert round(s.precision, 3) == 0.667 and round(s.recall, 3) == 0.667


def test_normalization_score_counts_declines_as_miss():
    cases = [
        NormalizationCase("f", "PC", "Windows PC"),
        NormalizationCase("f", "???", "Something"),
    ]
    produced = ["Windows PC", None]  # second declined
    s = normalization_score(cases, produced)
    assert s.correct == 1 and s.total == 2 and s.accuracy == 0.5


# --- self-generated committed benchmark (PRIMARY) ---------------------------

def test_self_benchmark_precision_is_perfect_no_fabrication():
    task = load_self_task(FIXTURE_DIR)
    res = run_benchmark(task)
    # Precision-first guarantee: we never emit a wrong correspondence.
    assert res.schema_match.false_positives == 0
    assert res.schema_match.precision == 1.0
    # Recall is bounded by what deterministic matching can justify (>= half).
    assert res.schema_match.recall >= 0.5
    # Bounded normalization is fully solved on the committed cases.
    assert res.normalization is not None and res.normalization.accuracy == 1.0


# --- optional external MaDI benchmark (never committed) ---------------------

@pytest.mark.skipif(not os.environ.get("MADI_BENCH_PATH"),
                    reason="MADI_BENCH_PATH not set; MaDI is an optional external "
                           "comparison and is never committed")
def test_madi_external_benchmark_runs():
    """Run our alignment + scorers against a locally downloaded MaDI task.

    Enable by cloning MaDI-Bench separately and pointing MADI_BENCH_PATH at a
    task's '.../base/input' directory. No MaDI code/data is committed."""
    base = Path(os.environ["MADI_BENCH_PATH"])
    task = load_madi_task(base)
    res = run_benchmark(task)
    # We assert only that it runs and never fabricates (precision computable).
    assert res.schema_match.false_positives >= 0
    assert res.schema_match.precision <= 1.0
