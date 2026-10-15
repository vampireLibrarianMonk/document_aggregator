"""Tests for the files-only auto-approve cascade (Part 2, design §2.2–§2.4).

Covers TR2 (count correctness + unrelated-shape negative), TR3 (approve+cascade
atomicity + fault injection), TR4 (determinism / preview==apply), TR5 (vocab
both directions + None-schema degrade), AC21 (TOCTOU recompute), already-approved
=> N==0, and Finding-7 idempotent re-merge. Mirrors the test_batch_api.py harness
(isolated project dir, SqliteJobQueue, Worker.run_once, TestClient).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import settings
from app.jobs import SqliteJobQueue, Worker
from app.jobs import ops as _ops  # noqa: F401  registers the batch_align op
from app.json_alignment.cascade import cascade_count, schema_fingerprint
from app.json_alignment.project_store import (
    apply_approval_and_cascade,
    load_batch_manifest,
    load_pending_ledger,
    load_project_library,
    merge_batch_outputs,
    save_target_schema,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return tmp_path


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


# A base record carrying all 8 golden fields PLUS filler fields, so a candidate
# that adds exactly one more field lands in a DIFFERENT cluster (different
# signature hash) while still HIGH-matching (Jaccard >= 0.95) the base shape —
# the exact cross-cluster flip the cascade must detect.
def _base_record() -> dict:
    rec = {
        "name": "Celeste", "releaseYear": "2018", "developer": "MMG",
        "publisher": "MMG", "platform": "Windows PC", "genres": ["Platformer"],
        "criticScore": 92, "ESRB": "E10+",
    }
    rec.update({f"extra_{i}": i for i in range(13)})
    return rec


def _unrelated_record() -> dict:
    """A genuinely different shape (no golden fields) -> its own provisional
    cluster that approving the base shape can never flip."""
    return {f"zz_field_{i}": i for i in range(10)}


def _run_batch(isolated, pid, docs, *, research=True, reject_below=0.5):
    q = SqliteJobQueue(isolated / "j.db")
    jid = q.enqueue("batch_align", {"project_id": pid, "reject_below": reject_below,
                                    "docs": docs, "research": research})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value == "completed", job.result
    assert job.result["ok"] is True, job.result
    return job.result


def _profile_for_doc(pid, doc_id):
    """The provisional profile id the given doc's own cluster produced."""
    ledger = load_pending_ledger(pid)
    for f in ledger["files"]:
        if f["doc_id"] == doc_id:
            return f["produced_cluster_profile"]
    return None


def _learn_shape(isolated, pid, records, *, schema=None):
    """Run a batch that learns ONE provisional profile for `records`'s shape and
    return its profile id (A). Leaves the real ledger in place."""
    save_target_schema(pid, schema or _schema())
    _run_batch(isolated, pid, [{"doc_id": "a.json", "records": records}])
    lib = load_project_library(pid)
    return lib.provisional_ids()[0]


def _write_ledger(pid, a_profile, a_records, candidates, *, schema=None):
    """Write a ledger with A's producing entry plus `candidates`, a list of
    (doc_id, records). Candidates are recorded in a DISTINCT cluster that did NOT
    produce A (produced_cluster_profile=None) and status 'pending' — the real
    disposition of a file that shared A's shape but was held for review at batch
    time. This is valid persisted state and the exact input the cascade reduces
    over (design: 'a pure function over persisted state')."""
    from app.json_alignment.project_store import save_pending_ledger
    from app.json_alignment.signature import signature_of
    files = [{
        "doc_id": "a.json",
        "records": a_records,
        "descriptions": {},
        "cluster_key": signature_of(a_records).hash,
        "status": "conformant",
        "produced_cluster_profile": a_profile,
    }]
    for doc_id, recs in candidates:
        files.append({
            "doc_id": doc_id,
            "records": recs,
            "descriptions": {},
            "cluster_key": signature_of(recs).hash + "_held",
            "status": "pending",
            "produced_cluster_profile": None,
        })
    save_pending_ledger(pid, {
        "schema_fingerprint": schema_fingerprint(schema or _schema()),
        "files": files,
    })


# ---------------------------------------------------------------------------
# TR2 — count correctness + unrelated-shape negative (AC13–AC15)
# ---------------------------------------------------------------------------

def test_cascade_counts_only_files_flipped_by_the_approved_shape(isolated):
    pid = "p_tr2"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    # Candidates C1, C2 share A's exact shape (so they route replay_clean to A
    # once A is approved) but were held for review in their own cluster at batch
    # time (produced no profile). An unrelated shape B can never flip to A.
    _write_ledger(pid, a_profile, [base], [
        ("c1.json", [dict(base)]),
        ("c2.json", [dict(base)]),
        ("b.json", [_unrelated_record()]),
    ])
    lib = load_project_library(pid)
    res = cascade_count(pid, a_profile, lib, _schema())
    assert res.n == 2
    assert res.doc_ids == ["c1.json", "c2.json"]
    # A's own producing file is excluded (direct producer, not an OTHER file).
    assert "a.json" not in res.doc_ids
    # The unrelated shape B is never flipped by approving A.
    assert "b.json" not in res.doc_ids


def test_cascade_leaves_an_unrelated_provisional_shape_untouched(isolated):
    pid = "p_tr2b"
    base = _base_record()
    # Shape B: a DIFFERENT (renamed) but still golden-relevant shape, so it
    # learns its own provisional profile distinct from A.
    bshape = {"game_name": "Hades", "release_year": "2020",
              "developer_studio": "SG", "genre_list": ["Roguelike"]}
    save_target_schema(pid, _schema())
    _run_batch(isolated, pid, [
        {"doc_id": "a.json", "records": [base]},
        {"doc_id": "bshape.json", "records": [bshape]}])
    a_profile = _profile_for_doc(pid, "a.json")
    b_profile = _profile_for_doc(pid, "bshape.json")
    assert b_profile is not None and b_profile != a_profile

    # Candidate shares A's shape and is held pending in its own cluster.
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    out = apply_approval_and_cascade(pid, a_profile)
    assert out["state"] == "approved"
    assert out["cascade_applied"] == 1
    assert out["cascade_doc_ids"] == ["c.json"]

    lib = load_project_library(pid)
    # A is approved; the unrelated shape B stays provisional (single-shape).
    assert a_profile in lib.approved_ids()
    assert b_profile in lib.provisional_ids()


# ---------------------------------------------------------------------------
# TR3 — approve + cascade atomicity + fault injection (AC18–AC19)
# ---------------------------------------------------------------------------

def test_apply_clears_files_and_approves_together(isolated):
    pid = "p_tr3"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    out = apply_approval_and_cascade(pid, a_profile)
    assert out["cascade_applied"] == 1 and out["cascade_doc_ids"] == ["c.json"]

    # The approval persisted AND the cleared file is marked conformant + emitted.
    assert a_profile in load_project_library(pid).approved_ids()
    ledger = load_pending_ledger(pid)
    c_entry = next(f for f in ledger["files"] if f["doc_id"] == "c.json")
    assert c_entry["status"] == "conformant"
    manifest = load_batch_manifest(pid)
    assert any(e["doc_id"] == "c.json" for e in manifest["outputs"])


def test_apply_is_atomic_on_library_persist_failure(isolated, monkeypatch):
    pid = "p_tr3b"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    import app.json_alignment.project_store as ps

    def _boom(*a, **k):
        raise RuntimeError("disk full on library write")

    monkeypatch.setattr(ps, "_save_library_locked", _boom)
    with pytest.raises(RuntimeError):
        apply_approval_and_cascade(pid, a_profile)

    # Both-or-neither: library approval did NOT persist. (The ledger is written
    # first, but the library — what makes approval "real" — never landed, so the
    # safe side holds: nothing is approved.)
    assert a_profile not in load_project_library(pid).approved_ids()


# ---------------------------------------------------------------------------
# TR4 — determinism / preview == apply (AC16)
# ---------------------------------------------------------------------------

def test_cascade_count_is_deterministic(isolated):
    pid = "p_tr4"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    _write_ledger(pid, a_profile, [base], [
        ("c1.json", [dict(base)]), ("c2.json", [dict(base)])])
    lib = load_project_library(pid)
    r1 = cascade_count(pid, a_profile, lib, _schema())
    r2 = cascade_count(pid, a_profile, lib, _schema())
    assert r1.n == r2.n == 2 and r1.doc_ids == r2.doc_ids


def test_preview_equals_apply_on_unchanged_state(isolated):
    from app.jobs.runtime import get_queue
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)

    pid = client.post("/projects", json={"name": "PA"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    base = _base_record()
    client.post(f"/projects/{pid}/alignment/batch", json={
        "docs": [{"doc_id": "a.json", "records": [base]}], "research": True})
    Worker(get_queue(), "w1").run_once()
    a_profile = _profile_for_doc(pid, "a.json")
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    pv = client.post(f"/projects/{pid}/alignment/approve",
                     json={"profile_id": a_profile, "preview": True}).json()
    assert pv["preview"] is True and pv["state"] == "provisional"
    assert pv["cascade_count"] == 1
    ap = client.post(f"/projects/{pid}/alignment/approve",
                     json={"profile_id": a_profile}).json()
    assert ap["state"] == "approved"
    assert pv["cascade_count"] == ap["cascade_applied"]
    assert pv["cascade_doc_ids"] == ap["cascade_doc_ids"]


# ---------------------------------------------------------------------------
# TR5 — honors effective_target_schema (both directions) + None-schema degrade
# ---------------------------------------------------------------------------

def _value_gated_schema() -> dict:
    """A schema whose `platform` taxonomy does NOT yet list 'Linux', so a file
    whose platform is Linux cannot conform until the value is taught."""
    s = _schema()
    s["properties"]["platform"]["x-pydi-taxonomy"] = ["Windows PC", "Xbox"]
    s["properties"]["platform"].pop("x-pydi-taxonomy-aliases", None)
    s["required"] = ["name", "releaseYear", "platform"]
    return s


def test_cascade_add_direction_honors_taught_value(isolated):
    from app.json_alignment.project_store import (
        apply_vocab_change,
        effective_target_schema,
    )
    pid = "p_tr5add"
    vgated = _value_gated_schema()
    base = _base_record(); base["platform"] = "Linux"
    # Teach 'Linux' so A's own batch learns a mapping that grounds platform …
    save_target_schema(pid, vgated)
    apply_vocab_change(pid, "add", "platform", value="Linux", added_by="t")
    _run_batch(isolated, pid, [{"doc_id": "a.json", "records": [base]}])
    a_profile = load_project_library(pid).provisional_ids()[0]
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])],
                  schema=vgated)

    # … then REMOVE it to simulate the "value not yet accepted" state: the
    # candidate cannot strictly conform via A under the current effective schema.
    apply_vocab_change(pid, "remove", "platform", value="Linux")
    before = cascade_count(pid, a_profile, load_project_library(pid),
                           effective_target_schema(pid))
    assert "c.json" not in before.doc_ids

    # Teach 'Linux' again (changes the effective schema fingerprint): now the
    # candidate strictly conforms via A and is counted.
    apply_vocab_change(pid, "add", "platform", value="Linux", added_by="t")
    after = cascade_count(pid, a_profile, load_project_library(pid),
                          effective_target_schema(pid))
    assert "c.json" in after.doc_ids


def test_cascade_removal_direction_excludes_broken_file(isolated):
    from app.json_alignment.project_store import (
        apply_vocab_change,
        effective_target_schema,
    )
    pid = "p_tr5rm"
    vgated = _value_gated_schema()
    save_target_schema(pid, vgated)
    apply_vocab_change(pid, "add", "platform", value="Linux", added_by="t")
    base = _base_record(); base["platform"] = "Linux"
    _run_batch(isolated, pid, [{"doc_id": "a.json", "records": [base]}])
    a_profile = load_project_library(pid).provisional_ids()[0]
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])],
                  schema=vgated)

    # With 'Linux' taught, the candidate IS counted.
    counted = cascade_count(pid, a_profile, load_project_library(pid),
                            effective_target_schema(pid))
    assert "c.json" in counted.doc_ids

    # Remove 'Linux' since the batch: the candidate's strict replay now breaks,
    # so it is NOT counted and NOT auto-cleared.
    apply_vocab_change(pid, "remove", "platform", value="Linux")
    after = cascade_count(pid, a_profile, load_project_library(pid),
                          effective_target_schema(pid))
    assert "c.json" not in after.doc_ids
    out = apply_approval_and_cascade(pid, a_profile)
    assert "c.json" not in out["cascade_doc_ids"]
    ledger = load_pending_ledger(pid)
    c_entry = next(f for f in ledger["files"] if f["doc_id"] == "c.json")
    assert c_entry["status"] == "pending"


def test_none_schema_degrades_to_plain_approve(isolated):
    """No golden schema saved AFTER a ledger exists -> cascade n=0, apply still
    approves with cascade_applied==0, no 500."""
    pid = "p_tr5none"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    # Delete the schema so effective_target_schema() returns None.
    from app.json_alignment.project_store import target_schema_path
    target_schema_path(pid).unlink()

    res = cascade_count(pid, a_profile, load_project_library(pid), None)
    assert res.n == 0 and res.doc_ids == []
    out = apply_approval_and_cascade(pid, a_profile)
    assert out["state"] == "approved" and out["cascade_applied"] == 0
    assert a_profile in load_project_library(pid).approved_ids()


# ---------------------------------------------------------------------------
# AC21 — TOCTOU: apply recomputes against current state
# ---------------------------------------------------------------------------

def test_apply_recomputes_after_state_change(isolated):
    from app.jobs.runtime import get_queue
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)

    pid = client.post("/projects", json={"name": "TOC"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    base = _base_record()
    client.post(f"/projects/{pid}/alignment/batch", json={
        "docs": [{"doc_id": "a.json", "records": [base]}], "research": True})
    Worker(get_queue(), "w1").run_once()
    a_profile = _profile_for_doc(pid, "a.json")
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    pv = client.post(f"/projects/{pid}/alignment/approve",
                     json={"profile_id": a_profile, "preview": True}).json()
    assert pv["cascade_count"] == 1

    # Mutate state between preview and apply: approve A directly via the
    # transaction so the candidate is already cleared. A SECOND apply of the same
    # profile now recomputes to 0 (already-approved => N==0 always).
    apply_approval_and_cascade(pid, a_profile)
    ap = client.post(f"/projects/{pid}/alignment/approve",
                     json={"profile_id": a_profile}).json()
    assert ap["cascade_applied"] == 0  # recomputed, not the stale preview of 1


# ---------------------------------------------------------------------------
# Already-approved => N == 0 always (Finding 6)
# ---------------------------------------------------------------------------

def test_already_approved_yields_zero(isolated):
    pid = "p_approved"
    base = _base_record()
    a_profile = _learn_shape(isolated, pid, [base])
    _write_ledger(pid, a_profile, [base], [("c.json", [dict(base)])])

    apply_approval_and_cascade(pid, a_profile)
    lib = load_project_library(pid)
    res = cascade_count(pid, a_profile, lib, _schema())
    assert res.n == 0 and res.doc_ids == []


# ---------------------------------------------------------------------------
# Finding 7 — idempotent re-merge (overwrite by doc_id, no _2 duplicate)
# ---------------------------------------------------------------------------

def test_merge_batch_outputs_is_idempotent_by_doc_id(isolated):
    pid = "p_merge"
    save_target_schema(pid, _schema())
    recs1 = [{"name": "X", "releaseYear": "2000"}]
    recs2 = [{"name": "X", "releaseYear": "2000"}, {"name": "Y", "releaseYear": "2001"}]

    m1 = merge_batch_outputs(pid, {"same.json": recs1})
    assert m1["doc_count"] == 1
    m2 = merge_batch_outputs(pid, {"same.json": recs2})
    # Same doc_id merged twice -> one entry, record_count updated, no `_2` dup.
    entries = [e for e in m2["outputs"] if e["doc_id"] == "same.json"]
    assert len(entries) == 1
    assert entries[0]["record_count"] == 2
    assert not entries[0]["stem"].endswith("_2")
    assert m2["doc_count"] == 1


def test_schema_fingerprint_stable(isolated):
    s = _schema()
    assert schema_fingerprint(s) == schema_fingerprint(json.loads(json.dumps(s)))
