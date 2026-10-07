"""Tests for batch clustering + per-cluster pathway decisions (Phase C batch).

The batch engine groups incoming docs by source shape and decides ONE pathway
per cluster (replay / drift-repair / novel-research / review / reject). These
lock the routing policy and the no-force-conversion guarantee: an unrelated
cluster is quarantined, never pushed into the golden shape.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.batch import (
    PATHWAY_DRIFT_REPAIR,
    PATHWAY_NOVEL,
    PATHWAY_REJECT,
    PATHWAY_REPLAY,
    PATHWAY_REVIEW,
    SourceDoc,
    cluster_documents,
    plan_batch,
)
from app.json_alignment.drift import FillBaseline
from app.json_alignment.executor import execute_mapping
from app.json_alignment.mapping import infer_mapping
from app.json_alignment.profile_store import ProfileLibrary, build_profile
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


def _approved_library(name: str, records: list[dict], descs=None) -> ProfileLibrary:
    """A library with one APPROVED profile learned from `records`, carrying a
    fill-rate baseline in its meta."""
    schema = _schema()
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    src = profile_source(records, descs)
    tgt = extract_target(schema)
    m = infer_mapping(src, tgt)
    _, prov = execute_mapping(records, m, tgt)
    prof = build_profile(name, src, tgt, m,
                         meta={"fill_baseline": FillBaseline.from_provenance(prov).to_dict()})
    lib.register(name, signature_of(records), prof)
    lib.approve(name)
    return lib


# -- clustering --------------------------------------------------------------

def test_same_shape_clusters_together_regardless_of_values():
    docs = [
        SourceDoc("a", [{"name": "X", "year": 2001}]),
        SourceDoc("b", [{"name": "Y", "year": 1999}]),       # same shape
        SourceDoc("c", [{"different": "shape"}]),            # different shape
    ]
    clusters = cluster_documents(docs)
    assert len(clusters) == 2
    big = max(clusters, key=lambda c: c.doc_count)
    assert set(big.doc_ids) == {"a", "b"}


def test_clustering_is_deterministic_first_seen_order():
    docs = [
        SourceDoc("a", [{"p": 1}]),
        SourceDoc("b", [{"q": 1}]),
        SourceDoc("c", [{"p": 2}]),
    ]
    clusters = cluster_documents(docs)
    # first-seen hash order: a's shape, then b's shape; c joins a's cluster
    assert clusters[0].doc_ids == ["a", "c"]
    assert clusters[1].doc_ids == ["b"]


# -- replay_clean ------------------------------------------------------------

def test_known_approved_shape_replays_clean():
    op = _vars()["level_09"]
    lib = _approved_library("opaque_v1", op.records, op.descriptions)
    docs = [SourceDoc(f"d{i}", [op.records[0]], op.descriptions) for i in range(5)]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5)
    assert len(report.decisions) == 1
    d = report.decisions[0]
    assert d.pathway == PATHWAY_REPLAY
    assert d.matched_profile == "opaque_v1"
    assert report.doc_counts() == {PATHWAY_REPLAY: 5}


# -- novel_research ----------------------------------------------------------

def test_novel_relevant_shape_routes_to_research():
    lib = ProfileLibrary(target_title="game")  # empty library
    docs = [SourceDoc("n", [{"name": "Z", "releaseYear": "2020",
                             "developer": "D", "genres": ["X"]}])]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5)
    assert report.decisions[0].pathway == PATHWAY_NOVEL


def test_novel_without_research_goes_to_review():
    lib = ProfileLibrary(target_title="game")
    docs = [SourceDoc("n", [{"name": "Z", "releaseYear": "2020"}])]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5,
                        research_available=False)
    assert report.decisions[0].pathway == PATHWAY_REVIEW


# -- reject_irrelevant -------------------------------------------------------

def test_unrelated_shape_is_quarantined_when_no_research():
    lib = ProfileLibrary(target_title="game")
    docs = [SourceDoc("junk", [{"weather": "sunny", "temp_c": 21}])]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5,
                        research_available=False)
    d = report.decisions[0]
    assert d.pathway == PATHWAY_REJECT
    assert report.quarantined() == [d]
    # the golden is never produced for a quarantined cluster
    assert "zero required golden fields" in " ".join(d.reasons)


def test_unrelated_shape_may_try_research_when_enabled():
    # With a research pathway and a schema that HAS required fields, even a
    # deterministic reject is offered to research rather than quarantined.
    lib = ProfileLibrary(target_title="game")
    docs = [SourceDoc("junk", [{"weather": "sunny", "temp_c": 21}])]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5,
                        research_available=True)
    assert report.decisions[0].pathway == PATHWAY_NOVEL


# -- drift_repair ------------------------------------------------------------

def test_known_shape_with_behavioral_drift_routes_to_drift_repair():
    # Learn+approve a profile where criticScore maps from a numeric source and
    # fills reliably. Then feed the SAME shape but with criticScore values that
    # no longer parse as numbers -> that field's fill rate collapses -> drift.
    schema = _schema()
    good = [{"name": "A", "releaseYear": "2001", "criticScore": 90},
            {"name": "B", "releaseYear": "2002", "criticScore": 88}]
    lib = _approved_library("plain_v1", good)

    # Same keys/shape (criticScore still an int-ish field by signature) but the
    # VALUES are non-numeric junk -> cast_number fails -> fill rate drops.
    drifted = [{"name": "C", "releaseYear": "2003", "criticScore": 95},
               {"name": "D", "releaseYear": "2004", "criticScore": 97}]
    # Mutate criticScore to strings that cannot cast to int.
    drifted = [{**r, "criticScore": "N/A"} for r in drifted]
    docs = [SourceDoc(f"x{i}", [r]) for i, r in enumerate(drifted)]

    report = plan_batch(docs, schema, lib, reject_below=0.5)
    d = report.decisions[0]
    # It still HIGH-matches the shape, but behavioral drift kicks it to repair.
    assert d.pathway == PATHWAY_DRIFT_REPAIR
    assert "criticScore" in d.broken_fields


# -- report rollups ----------------------------------------------------------

def test_batch_report_rolls_up_doc_counts_per_pathway():
    op = _vars()["level_09"]
    lib = _approved_library("opaque_v1", op.records, op.descriptions)
    docs = [SourceDoc(f"clean{i}", [op.records[0]], op.descriptions) for i in range(4)]
    docs.append(SourceDoc("junk", [{"unrelated": 1}]))  # reject (no research)
    report = plan_batch(docs, _schema(), lib, reject_below=0.5,
                        research_available=False)
    counts = report.doc_counts()
    assert counts[PATHWAY_REPLAY] == 4
    assert counts[PATHWAY_REJECT] == 1
    assert len(report.decisions) == 2
    # round-trips to a dict for the API/UI
    assert report.to_dict()["clusters"] == 2
