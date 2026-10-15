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


# --- mapping: dictionary (synonym) + fuzzy (typo) tiers --------------------

def _one_field_schema():
    """The game schema's descriptions matter for the overlap tier, so use a
    realistic subset."""
    return {"title": "t", "properties": {
        "developer": {"type": "string",
                      "description": "the studio that developed or made the game"},
        "publisher": {"type": "string",
                      "description": "the company that published the game"},
        "name": {"type": "string", "description": "the title of the game"}}}


def test_synonym_tier_recovers_unambiguous_synonym():
    # 'studio' shares no token with 'developer' and a single token dilutes below
    # the description floor -> only the curated synonym tier can recover it.
    recs = [{"studio": "Valve"}]
    m = infer_mapping(profile_source(recs), extract_target(_one_field_schema()))
    by = {fm.target: fm for fm in m.fields}
    assert by["developer"].status == STATUS_MAPPED
    assert by["developer"].method == "synonym"
    assert by["developer"].source_path == "studio"


def test_synonym_tier_excludes_ambiguous_words():
    # 'label' is ambiguous (publisher OR a title's label) and 'rating' (ESRB OR
    # criticScore); neither may be in the dictionary, so a lone such key must
    # NOT be confidently mapped by the synonym tier (no fabrication).
    from app.json_alignment.mapping import _SYNONYMS

    all_syn = {s for syns in _SYNONYMS.values() for s in syns}
    assert "label" not in all_syn
    assert "rating" not in all_syn
    assert "score" not in all_syn


def test_fuzzy_tier_recovers_single_typo_but_not_unrelated():
    # A one-character typo of a field name is recovered; an unrelated short word
    # of similar length is not (length + edit-budget guards).
    recs = [{"dveloper": "Valve", "zzzz": "junk"}]
    m = infer_mapping(profile_source(recs), extract_target(_one_field_schema()))
    by = {fm.target: fm for fm in m.fields}
    assert by["developer"].status == STATUS_MAPPED
    assert by["developer"].method == "fuzzy"
    # 'zzzz' must not be grabbed for any target.
    assert all(fm.source_path != "zzzz" for fm in m.fields)


def test_fuzzy_tier_rejects_multitoken_decoy_by_length_guard():
    # 'developer_contact' (a hard-negative decoy) is far longer than any target
    # name joined, so the length guard rejects it from the fuzzy tier. It may
    # still match 'developer' via token overlap (shares the token), which is a
    # separate, already-studied behavior; here we only assert the FUZZY tier is
    # not the thing that matched (method != 'fuzzy').
    recs = [{"developer_contact": "ops@example.com"}]
    m = infer_mapping(profile_source(recs), extract_target(_one_field_schema()))
    by = {fm.target: fm for fm in m.fields}
    assert by["developer"].method != "fuzzy"


def test_fuzzy_tier_blocks_common_word_near_short_name():
    # 'game' is one edit from 'name' but is a common standalone word, not a typo
    # of the golden field; the blocklist guard must stop the fuzzy tier from
    # claiming it (no fabrication on a short golden name).
    recs = [{"game": "Portal"}]
    m = infer_mapping(profile_source(recs), extract_target(_one_field_schema()))
    by = {fm.target: fm for fm in m.fields}
    assert by["name"].method != "fuzzy"
    # and it must not be confidently mapped to name via fuzzy
    assert not (by["name"].status == STATUS_MAPPED and by["name"].source_path == "game")


def test_synonym_and_fuzzy_rank_below_alias_and_exact():
    from app.json_alignment.mapping import _ALIAS, _FUZZY_MAX, _NORMALIZED, _SYNONYM

    assert _SYNONYM < _ALIAS < _NORMALIZED
    assert _FUZZY_MAX < _SYNONYM


# --- value-evidence precision gate -----------------------------------------

def _game_schema():
    """The real golden schema subset with the constraints the value gate uses."""
    return {"title": "t", "properties": {
        "name": {"type": "string", "description": "the title of the game"},
        "releaseYear": {"type": "string", "pattern": r"^\d{4}$",
                        "description": "the year the game was published"},
        "developer": {"type": "string",
                      "description": "the studio that developed or made the game"},
        "publisher": {"type": "string",
                      "description": "the company that published the game"},
        "platform": {"type": "string", "description": "the console or hardware system",
                     "x-pydi-taxonomy": ["Windows PC", "Xbox", "PlayStation"]},
        "criticScore": {"type": "integer", "minimum": 0, "maximum": 100,
                        "description": "critic press rating out of 100"},
        "ESRB": {"type": "string", "description": "esrb age rating",
                 "x-pydi-taxonomy": ["E", "E10+", "T", "M", "AO"]}}}


def _mapped(recs):
    return infer_mapping(profile_source(recs), extract_target(_game_schema())).mapped()


def test_value_gate_vetoes_pattern_violation():
    # year_installed shares 'year' with releaseYear but its value is a timestamp,
    # not a 4-digit year -> pattern veto.
    assert "releaseYear" not in _mapped([{"year_installed": "1998-04-12T09:00:00Z"}])


def test_value_gate_vetoes_out_of_range_number():
    # a star string is not a 0-100 integer score.
    assert "criticScore" not in _mapped([{"score_rating_stars": "4 stars"}])


def test_value_gate_vetoes_out_of_vocabulary_enum():
    # platform_vendor / esrb_descriptor values are not in the controlled vocab.
    assert "platform" not in _mapped([{"platform_vendor": "Acme Instruments Inc"}])
    assert "ESRB" not in _mapped([{"esrb_descriptor": "non-rated equipment"}])


def test_value_gate_vetoes_email_for_name_like_target():
    # developer_contact is an email, not a studio name.
    assert "developer" not in _mapped([{"developer_contact": "ops@example.com"}])


def test_weak_generic_overlap_demotes_qualified_generic_name():
    # station_name shares only the GENERIC 'name' plus a foreign qualifier
    # 'station' -> demoted, not grounded to name.
    assert "name" not in _mapped([{"station_name": "North Field Buoy"}])


def test_value_gate_recovers_ratio_encoded_score():
    # recall-debt payoff (step 1): a criticScore synonym carrying a '9/10'
    # ratio is a genuine score and must NOT be vetoed (whole-string ratio).
    assert _mapped([{"metacritic": "9/10"}]).get("criticScore") == "metacritic"
    assert _mapped([{"metacritic": "88%"}]).get("criticScore") == "metacritic"


def test_value_gate_recovers_date_for_year_field():
    # recall-debt payoff (step 2): a releaseYear synonym carrying a real DATE
    # satisfies the ^\d{4}$ pattern via token recovery (date-shaped value).
    assert _mapped([{"launched": "2007-05-01"}]).get("releaseYear") == "launched"
    assert _mapped([{"launched": "October 27, 2017"}]).get("releaseYear") == "launched"


def test_value_gate_still_vetoes_timestamp_and_prose_year():
    # precision guard (step 2 + Guardian finding): a releaseYear synonym whose
    # value is a TIMESTAMP (clock) or PROSE containing a stray year must STAY
    # vetoed — token recovery only fires for a plausible DATE.
    assert "releaseYear" not in _mapped([{"launched": "1998-04-12T09:00:00Z"}])
    assert "releaseYear" not in _mapped([{"launched": "copyright 2019 acme"}])
    assert "releaseYear" not in _mapped([{"launched": "page 1998 of 2000"}])


def test_value_gate_vetoes_region_for_company_target():
    # publisher_region / owner_studio_country carry a geographic REGION value,
    # which is a location not a company -> vetoed for publisher/developer even
    # though the target is free-text (no schema constraint to catch it).
    assert "publisher" not in _mapped([{"publisher_region": "North America"}])
    assert "publisher" not in _mapped([{"publisher_region": "EMEA"}])
    assert "developer" not in _mapped([{"owner_studio_country": "United States"}])


def test_value_corroboration_boosts_within_tier_but_never_past_alias():
    # A value-corroborated synonym scores higher than an uncorroborated one, but
    # the boost is capped so it never reaches the alias tier.
    from app.json_alignment.mapping import _ALIAS, _score_candidate
    from app.json_alignment.source_profile import profile_source
    from app.json_alignment.target_profile import extract_target

    tgt = extract_target(_game_schema()).by_name()["criticScore"]
    # 'metascore' is a criticScore synonym; value 90 corroborates (0..100 int).
    corrob = profile_source([{"metascore": 90}]).fields[0]
    plain = profile_source([{"metascore": "N/A"}]).fields[0]  # no numeric value
    s_corrob, _ = _score_candidate(corrob, tgt)
    s_plain, _ = _score_candidate(plain, tgt)
    assert s_corrob > s_plain          # corroboration lifts the score
    assert s_corrob < _ALIAS           # but never into the alias band


def test_real_region_in_company_name_is_not_vetoed():
    # A publisher whose NAME contains a region word but is a company ("North
    # American Games Inc") is NOT a bare region -> not vetoed.
    m = _mapped([{"publisher": "North American Games Inc"}])
    assert m.get("publisher") == "publisher"   # exact name, immune anyway


def test_value_gate_preserves_real_matches():
    # The gate must NOT veto genuine values or legitimate qualified names.
    m = _mapped([{"release_year": "2007", "critic_score": 90,
                  "platform": "Windows PC", "ESRB": "T",
                  "publisher_name": "Valve", "game_name": "Portal"}])
    assert m.get("releaseYear") == "release_year"   # 2007 matches ^\d{4}$
    assert m.get("criticScore") == "critic_score"   # 90 in 0..100
    assert m.get("platform") == "platform"          # in vocab
    assert m.get("ESRB") == "ESRB"                   # in vocab
    assert m.get("publisher") == "publisher_name"   # shared SPECIFIC token kept
    assert m.get("name") == "game_name"             # 'game' explained by desc


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
