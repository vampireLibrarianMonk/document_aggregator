"""Routing + execution tests for human-rejected shapes.

A rejected shape must never resurrect as a provisional approval card: it routes
to PATHWAY_DECLINED (emits nothing, learns nothing), the per-doc progress tick
still fires for its docs, and restoring the shape makes it learnable again.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.batch import (
    PATHWAY_DECLINED,
    PATHWAY_NOVEL,
    SourceDoc,
    cluster_documents,
    decide_pathway,
    plan_batch,
)
from app.json_alignment.batch_agent import (
    _batch_convergence_report,
    _learn_profile,
    execute_cluster,
    run_batch,
)
from app.json_alignment.mapping import Mapping
from app.json_alignment.profile_store import (
    STATE_REJECTED,
    ConversionProfile,
    ProfileLibrary,
)
from app.json_alignment.signature import signature_of
from app.json_alignment.target_profile import extract_target

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


_NOVEL = {"name": "Z", "releaseYear": "2020", "developer": "D", "genres": ["X"]}


def _rejected_library_for(records: list[dict], pid: str = "game__shape_rej",
                          category: str = "not_our_data") -> ProfileLibrary:
    """A library holding one REJECTED entry whose signature matches `records`."""
    schema = _schema()
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    sig = signature_of(records)
    prof = ConversionProfile(name=pid, target_title="game", mapping=Mapping(fields=[]))
    lib.register(pid, sig, prof)
    lib.reject(pid, category=category, fields=None, note="",
               composed_text="Rejected — not our data.")
    return lib


# -- decide_pathway ----------------------------------------------------------

def test_same_signature_as_rejected_routes_declined():
    lib = _rejected_library_for([_NOVEL])
    docs = [SourceDoc("d1", [_NOVEL])]
    report = plan_batch(docs, _schema(), lib, reject_below=0.5)
    d = report.decisions[0]
    assert d.pathway == PATHWAY_DECLINED
    assert d.matched_profile == "game__shape_rej"
    assert "rejected profile" in " ".join(d.reasons)


def test_moderate_variant_of_rejected_routes_declined():
    # A near-variant: drop one key so the path-set Jaccard lands in MODERATE but
    # not HIGH. Still declined because a human's rejection dominates.
    variant = dict(_NOVEL)
    variant["extra_field"] = "noise"
    lib = _rejected_library_for([_NOVEL])
    docs = [SourceDoc("d1", [variant])]
    d = decide_pathway(cluster_documents(docs)[0], _schema(), lib,
                       reject_below=0.5)
    assert d.pathway == PATHWAY_DECLINED


def test_no_rejected_entries_is_unchanged():
    lib = ProfileLibrary(target_title="game")  # empty, no rejections
    docs = [SourceDoc("n", [_NOVEL])]
    d = decide_pathway(cluster_documents(docs)[0], _schema(), lib,
                       reject_below=0.5)
    assert d.pathway == PATHWAY_NOVEL


# -- execute_cluster / _done -------------------------------------------------

def test_declined_cluster_emits_nothing_and_ticks_progress():
    lib = _rejected_library_for([_NOVEL])
    docs = [SourceDoc("d1", [_NOVEL]), SourceDoc("d2", [_NOVEL])]
    cluster = cluster_documents(docs)[0]
    decision = decide_pathway(cluster, _schema(), lib, reject_below=0.5)
    ticked: list[str] = []
    outcome = execute_cluster(decision, cluster, _schema(), lib,
                              on_doc_done=ticked.append)
    assert outcome.pathway == PATHWAY_DECLINED
    assert outcome.records == []
    assert outcome.quarantined is False
    assert outcome.provisional_profile is None
    # per-doc progress ticked once per doc in the cluster
    assert ticked == ["d1", "d2"]


def test_declined_maps_to_conflict_in_convergence_report():
    lib = _rejected_library_for([_NOVEL])
    docs = [SourceDoc("d1", [_NOVEL])]
    result = run_batch(docs, _schema(), lib, reject_below=0.5)
    report = _batch_convergence_report(result)
    statuses = [f["status"] for f in report["sections"][0]["fields"]]
    assert statuses == ["conflict"]


# -- integration: reject -> rerun declined -> restore -> rerun learnable -----

def test_rejected_shape_does_not_resurface_then_restores():
    lib = _rejected_library_for([_NOVEL])
    docs = [SourceDoc("d1", [_NOVEL])]

    # rerun same shape -> declined, no provisional minted, entry stays rejected
    result = run_batch(docs, _schema(), lib, reject_below=0.5)
    assert result.outcomes[0].pathway == PATHWAY_DECLINED
    assert lib.provisional_ids() == []
    assert lib.get("game__shape_rej").state == STATE_REJECTED

    # restore -> the shape becomes learnable (routes to novel/research) again
    lib.restore("game__shape_rej")
    result2 = run_batch(docs, _schema(), lib, reject_below=0.5,
                        research_available=True)
    assert result2.outcomes[0].pathway == PATHWAY_NOVEL


# -- defensive backstop in _learn_profile ------------------------------------

def test_learn_profile_backstop_refuses_rejected_entry():
    lib = _rejected_library_for([_NOVEL])
    cluster = cluster_documents([SourceDoc("d1", [_NOVEL])])[0]
    _, registered = _learn_profile(cluster, _schema(), lib, pid="game__shape_rej")
    assert registered is False
    # still rejected, not overwritten into provisional
    assert lib.get("game__shape_rej").state == STATE_REJECTED
