"""End-to-end tests for the batch JSON->golden API + worker op (Phase API).

Flow: set the golden schema -> submit a batch -> run the worker -> poll the job
-> inspect the learned library -> approve a provisional profile -> a second
batch of the same shape replays. Also locks: no schema set -> 400; the op grows
and persists the per-project library; quarantine/review emit no gold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import settings
from app.jobs import SqliteJobQueue, Worker
from app.jobs import ops as _ops  # noqa: F401  registers ops
from app.json_alignment.project_store import (
    load_project_library,
)
from app.json_alignment.variation import generate_variations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    # keep the shared job-queue singleton from leaking across tests
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return tmp_path


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _opaque_shape():
    golden = json.loads((VAR_DIR / "golden_records.json").read_text())
    v = {x.name: x for x in generate_variations(golden, _schema(), seed=42)}["level_09"]
    return v.records, v.descriptions


# -- op via worker -----------------------------------------------------------

def test_batch_op_requires_target_schema(isolated):
    q = SqliteJobQueue(isolated / "j.db")
    jid = q.enqueue("batch_align", {"project_id": "p_none", "docs": [{"records": [{"a": 1}]}]})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value == "completed"
    assert job.result["ok"] is False
    assert "schema" in job.result["error"]


def test_batch_op_runs_and_persists_library(isolated):
    from app.json_alignment.project_store import save_target_schema

    pid = "p1"
    save_target_schema(pid, _schema())
    recs, descs = _opaque_shape()
    docs = [{"doc_id": f"d{i}", "records": [recs[0]], "descriptions": descs}
            for i in range(3)]
    docs.append({"doc_id": "junk", "records": [{"weather": "sunny"}]})

    q = SqliteJobQueue(isolated / "j.db")
    jid = q.enqueue("batch_align", {"project_id": pid, "reject_below": 0.5,
                                    "docs": docs, "research": False})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value == "completed" and job.result["ok"] is True
    summary = job.result["summary"]
    # the 3 opaque docs are a novel shape (empty library) -> research off ->
    # they go to review; junk -> quarantine. Nothing fabricated.
    assert summary["quarantined_docs"] == 1
    # the library persisted to disk
    assert load_project_library(pid).entries is not None


def test_registered():
    from app.jobs.worker import registered_ops
    assert "batch_align" in registered_ops()


# -- full API flow -----------------------------------------------------------

def test_api_set_schema_submit_approve_then_replay(isolated):
    from app.jobs.runtime import get_queue
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)

    created = client.post("/projects", json={"name": "Batch"}).json()
    pid = created["id"]

    # no schema yet -> submit is rejected
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": [{"records": [{"a": 1}]}]})
    assert r.status_code == 400

    # set the golden schema
    r = client.put(f"/projects/{pid}/alignment/target-schema",
                   json={"target": _schema()})
    assert r.status_code == 200
    assert "name" in r.json()["required"]

    # submit a batch of a novel-but-relevant shape (plain golden keys). research
    # is on so a novel shape routes to research (-> provisional profile); the
    # LLM tier is a no-op offline, but deterministic inference still recovers
    # these plain keys and registers the profile for approval.
    shape = {"name": "Z", "releaseYear": "2020", "developer": "D", "genres": ["X"]}
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": [{"doc_id": "a", "records": [shape]},
                                   {"doc_id": "b", "records": [shape]}],
                          "research": True})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # run the worker against the SAME shared queue the API enqueued to
    Worker(get_queue(), "w1").run_once()
    status = client.get(f"/jobs/{job_id}").json()
    assert status["state"] == "completed"
    assert status["result"]["ok"] is True

    # the library now holds a provisional profile for the novel shape
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert lib["provisional"], "expected a provisional profile to approve"
    prov_id = lib["provisional"][0]

    # approve it (human sign-off)
    r = client.post(f"/projects/{pid}/alignment/approve",
                    json={"profile_id": prov_id})
    assert r.status_code == 200 and r.json()["state"] == "approved"

    # a second batch of the SAME shape now replays deterministically
    r2 = client.post(f"/projects/{pid}/alignment/batch",
                     json={"docs": [{"doc_id": "c", "records": [shape]}],
                           "research": False})
    job2 = r2.json()["job_id"]
    Worker(get_queue(), "w1").run_once()
    s2 = client.get(f"/jobs/{job2}").json()["result"]
    counts = s2["summary"]["doc_counts_by_pathway"]
    assert counts.get("replay_clean", 0) == 1
    assert s2["summary"]["conformed_records"] == 1
