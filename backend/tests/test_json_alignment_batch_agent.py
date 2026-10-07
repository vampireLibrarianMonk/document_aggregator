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
