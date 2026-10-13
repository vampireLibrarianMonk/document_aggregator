"""Tests for the batch pathway coordinator (Phase D).

Executes the per-cluster decisions plan_batch produced, through the command
center, concurrently. Locks the invariants: approved shapes replay with zero
re-inference; novel/drift shapes register PROVISIONAL profiles that await human
approval; review/reject clusters emit NO golden records (no fabrication); and
the command-center path matches the direct path exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.batch import SourceDoc
from app.json_alignment.batch_agent import (
    run_batch,
    run_batch_via_coordinator,
)
from app.json_alignment.drift import FillBaseline
from app.json_alignment.executor import execute_mapping
from app.json_alignment.mapping import infer_mapping
from app.json_alignment.profile_store import (
    STATE_PROVISIONAL,
    ProfileLibrary,
    build_profile,
)
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


def _library_with_approved_opaque() -> ProfileLibrary:
    schema = _schema()
    op = _vars()["level_09"]
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    src = profile_source(op.records, op.descriptions)
    tgt = extract_target(schema)
    m = infer_mapping(src, tgt)
    _, prov = execute_mapping(op.records, m, tgt)
    prof = build_profile("opaque_v1", src, tgt, m,
                         meta={"fill_baseline": FillBaseline.from_provenance(prov).to_dict()})
    lib.register("opaque_v1", signature_of(op.records), prof)
    lib.approve("opaque_v1")
    return lib


def _mixed_docs() -> list[SourceDoc]:
    op = _vars()["level_09"]
    docs = [SourceDoc(f"clean_{i}", [op.records[i % len(op.records)]], op.descriptions)
            for i in range(4)]
    docs.append(SourceDoc("novel", [{"name": "Z", "releaseYear": "2020",
                                     "developer": "D", "genres": ["X"]}]))
    docs.append(SourceDoc("junk", [{"weather": "sunny"}]))
    return docs


# -- replay ------------------------------------------------------------------

def test_approved_shape_replays_and_conforms():
    op = _vars()["level_09"]
    docs = [SourceDoc(f"d{i}", [op.records[0]], op.descriptions) for i in range(5)]
    res = run_batch(docs, _schema(), _library_with_approved_opaque(),
                    reject_below=0.5)
    o = res.outcomes[0]
    assert o.pathway == "replay_clean"
    assert len(o.records) == 5 and o.conformed == 5
    assert o.provisional_profile is None  # replay never re-learns


# -- quarantine emits no gold ------------------------------------------------

def test_quarantine_emits_no_golden_records():
    res = run_batch(_mixed_docs(), _schema(), _library_with_approved_opaque(),
                    reject_below=0.5, research_available=False)
    quarantined = [o for o in res.outcomes if o.quarantined]
    assert quarantined, "expected a quarantined cluster"
    for o in quarantined:
        assert o.records == []            # the golden is never produced
        assert o.conformed == 0


def test_review_cluster_emits_no_golden_records():
    res = run_batch(_mixed_docs(), _schema(), _library_with_approved_opaque(),
                    reject_below=0.5, research_available=False)
    review = [o for o in res.outcomes if o.pathway == "review"]
    assert review
    assert all(o.records == [] for o in review)


# -- novel -> provisional profile awaiting approval --------------------------

def test_novel_cluster_registers_provisional_profile():
    lib = _library_with_approved_opaque()
    docs = [SourceDoc("novel", [{"name": "Z", "releaseYear": "2020",
                                 "developer": "D", "genres": ["X"]}])]
    res = run_batch(docs, _schema(), lib, reject_below=0.5, research_available=True)
    o = res.outcomes[0]
    assert o.pathway == "novel_research"
    assert o.provisional_profile is not None
    # it is registered PROVISIONAL, not approved -> awaits human sign-off
    entry = lib.get(o.provisional_profile)
    assert entry is not None and entry.state == STATE_PROVISIONAL
    assert o.provisional_profile not in lib.approved_ids()


def test_approved_after_human_signoff_then_replays_with_zero_reinference():
    # Simulate the research -> approve -> settle loop: run novel, approve the
    # provisional profile, then a second batch of the SAME shape replays.
    lib = _library_with_approved_opaque()
    shape = [{"name": "Z", "releaseYear": "2020", "developer": "D", "genres": ["X"]}]
    first = run_batch([SourceDoc("n", shape)], _schema(), lib,
                      reject_below=0.5, research_available=True)
    pid = first.outcomes[0].provisional_profile
    assert pid is not None
    lib.approve(pid)  # human signs off once

    second = run_batch([SourceDoc("n2", shape), SourceDoc("n3", shape)], _schema(),
                       lib, reject_below=0.5, research_available=True)
    o = second.outcomes[0]
    assert o.pathway == "replay_clean"       # now streamlines deterministically
    assert o.provisional_profile is None     # no re-inference
    assert len(o.records) == 2


# -- coordinator parity ------------------------------------------------------

def test_coordinator_matches_direct_batch():
    direct = run_batch(_mixed_docs(), _schema(), _library_with_approved_opaque(),
                       reject_below=0.5, research_available=False)
    coord = run_batch_via_coordinator(
        _mixed_docs(), _schema(), _library_with_approved_opaque(),
        reject_below=0.5, research_available=False)
    assert coord.summary() == direct.summary()
    assert [o.to_dict() for o in coord.outcomes] == [o.to_dict() for o in direct.outcomes]


# -- determinism (parallel assembly) -----------------------------------------

def test_parallel_and_sequential_produce_same_outcomes():
    docs = _mixed_docs()
    par = run_batch(docs, _schema(), _library_with_approved_opaque(),
                    reject_below=0.5, research_available=False, parallel=True)
    seq = run_batch(docs, _schema(), _library_with_approved_opaque(),
                    reject_below=0.5, research_available=False, parallel=False)
    assert [o.to_dict() for o in par.outcomes] == [o.to_dict() for o in seq.outcomes]


# -- summary rollup ----------------------------------------------------------

def test_summary_rolls_up_counts():
    res = run_batch(_mixed_docs(), _schema(), _library_with_approved_opaque(),
                    reject_below=0.5, research_available=False)
    s = res.summary()
    assert s["doc_counts_by_pathway"]["replay_clean"] == 4
    assert s["conformed_records"] == 4
    assert s["quarantined_docs"] == 1


# -- STRICT conformance: a record missing a REQUIRED golden field is not
#    conformant, is NOT counted as conformed, and is NOT exported ------------

def test_record_missing_required_field_is_not_conformant_predicate():
    from app.json_alignment.pipeline import run_alignment

    schema = _schema()  # required: name, releaseYear
    # A shape that grounds releaseYear + others but has NO 'name' candidate.
    recs = [{"releaseYear": "2019", "developer": "Respawn", "genres": ["Shooter"]},
            {"releaseYear": "2016", "developer": "id", "genres": ["Shooter"]}]
    res = run_alignment(recs, schema)
    flags = res.conformant_flags()
    # validation.ok may be True (abstaining on a required field is not an ERROR),
    # but the record is NOT conformant: 'name' (required) is absent.
    assert all(not f for f in flags)
    for i, rec in enumerate(res.records):
        assert "name" not in rec
        assert res.validation[i].ok is True   # honest deferral, not a fab error


def test_approved_shape_with_missing_required_record_is_withheld_from_export():
    """When a learnable shape produces records missing a required field, those
    records count as needs_review (not conformed) and never reach the per-file
    export (`conformed_by_doc`)."""
    schema = _schema()
    # Build + approve a profile for a shape that maps everything EXCEPT name
    # (no name-like source key), so replayed records are valid-but-not-conformant.
    shape_recs = [{"releaseYear": "2019", "developer": "Respawn", "genres": ["Shooter"]}]
    src = profile_source(shape_recs, None)
    tgt = extract_target(schema)
    m = infer_mapping(src, tgt)
    _, prov = execute_mapping(shape_recs, m, tgt)
    prof = build_profile("noname_v1", src, tgt, m,
                         meta={"fill_baseline": FillBaseline.from_provenance(prov).to_dict()})
    lib = ProfileLibrary(target_title=tgt.title)
    lib.register("noname_v1", signature_of(shape_recs), prof)
    lib.approve("noname_v1")

    docs = [SourceDoc("teamA.json", shape_recs), SourceDoc("teamB.json", shape_recs)]
    res = run_batch(docs, schema, lib, reject_below=0.5)
    o = res.outcomes[0]
    assert o.pathway == "replay_clean"
    # Records were produced but NONE conform (missing required 'name').
    assert o.conformed == 0
    assert o.needs_review == 2
    assert o.records == []                      # nothing conformant to keep
    # And nothing reaches the per-file export.
    assert res.conformed_by_doc() == {}
    assert res.summary()["conformed_records"] == 0
    assert res.summary()["needs_review_records"] == 2
