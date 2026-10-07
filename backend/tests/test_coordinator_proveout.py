"""Light smoke tests guarding the prove-out claims (Phase F).

Not the full measurement run (that is _proveout.py / the doc) -- these lock the
load-bearing properties so a regression can't silently break the story:
  - the correction pipeline runs through the coordinator on a sample project;
  - a mass batch's model/human cost is bounded to DISTINCT SHAPES, not docs;
  - approving shapes once makes the next batch replay deterministically;
  - unrelated docs are quarantined, never fabricated.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import project as sc
from app.json_alignment.batch import SourceDoc
from app.json_alignment.batch_agent import run_batch_via_coordinator
from app.json_alignment.profile_store import ProfileLibrary
from app.json_alignment.target_profile import extract_target
from app.json_alignment.variation import generate_variations

FIX = Path(__file__).parent / "fixtures" / "json_alignment"


def _schema() -> dict:
    return json.loads((FIX / "target_schema.json").read_text())


def _mixed_batch(copies: int) -> tuple[list[SourceDoc], int]:
    schema = _schema()
    golden = json.loads((FIX / "variations" / "golden_records.json").read_text())
    variants = generate_variations(golden, schema, seed=42)
    docs: list[SourceDoc] = []
    for v in variants:
        for i in range(copies):
            docs.append(SourceDoc(f"{v.name}__{i}", [v.records[i % len(v.records)]],
                                  v.descriptions))
    for i in range(copies):
        docs.append(SourceDoc(f"junk__{i}", [{"weather": "sunny", "wind": i}]))
    return docs, len(variants)


def test_correction_runs_through_coordinator():
    report = sc.run_reconciliation(mode="draft", project_id="1",
                                   engine="coordinator")
    assert report["summary"]["total_units"] > 0


def test_batch_model_cost_bounded_to_shapes_not_docs():
    schema = _schema()
    docs, _ = _mixed_batch(40)  # 40 copies each -> hundreds of docs, few shapes
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    res = run_batch_via_coordinator(docs, schema, lib, reject_below=0.5,
                                    research_available=True)
    clusters = res.summary()["clusters"]
    # The number of distinct shapes (clusters) is far smaller than the doc count:
    # research/model work is per-cluster, not per-document.
    assert clusters < len(docs)
    assert clusters <= 12  # ~8 variant shapes + junk, regardless of copies


def test_approved_shapes_replay_on_second_batch():
    schema = _schema()
    docs, _ = _mixed_batch(20)
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    # cold: shapes are novel -> provisional profiles registered
    run_batch_via_coordinator(docs, schema, lib, reject_below=0.5,
                              research_available=True)
    assert lib.provisional_ids(), "cold batch should learn provisional shapes"
    for pid in lib.provisional_ids():
        lib.approve(pid)
    # warm: approved shapes now replay deterministically
    warm = run_batch_via_coordinator(docs, schema, lib, reject_below=0.5,
                                     research_available=True)
    counts = warm.summary()["doc_counts_by_pathway"]
    assert counts.get("replay_clean", 0) > 0


def test_unrelated_docs_quarantined_never_fabricated():
    schema = _schema()
    docs = [SourceDoc(f"junk{i}", [{"weather": "sunny"}]) for i in range(10)]
    lib = ProfileLibrary(target_title=extract_target(schema).title)
    res = run_batch_via_coordinator(docs, schema, lib, reject_below=0.5,
                                    research_available=False)
    # all junk -> quarantined, zero golden records produced
    assert res.summary()["quarantined_docs"] == 10
    assert res.summary()["produced_records"] == 0
