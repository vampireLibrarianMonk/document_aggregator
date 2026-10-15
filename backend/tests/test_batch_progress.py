"""Part 1 progress-channel tests: queue column + per-cluster callback + API shape.

Mirrors the batch harness in test_batch_api.py (isolated DATA_DIR, save schema,
enqueue batch_align, run Worker.run_once, poll via TestClient).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import settings
from app.jobs import SqliteJobQueue, Worker
from app.jobs import ops as _ops  # noqa: F401  registers ops
from app.json_alignment.batch import SourceDoc
from app.json_alignment.batch_agent import run_batch
from app.json_alignment.profile_store import ProfileLibrary

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return tmp_path


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


# -- (a) unit: set_progress round-trips through the queue --------------------

def test_set_progress_roundtrips(isolated):
    q = SqliteJobQueue(isolated / "j.db")
    jid = q.enqueue("batch_align", {"project_id": "p", "docs": []})
    q.set_progress(jid, 1, 3)
    assert q.get(jid).progress == {"done": 1, "total": 3}


def test_progress_absent_until_first_tick(isolated):
    q = SqliteJobQueue(isolated / "j.db")
    jid = q.enqueue("batch_align", {"project_id": "p", "docs": []})
    assert q.get(jid).progress is None


def test_progress_migration_on_existing_db(isolated):
    """A pre-existing jobs DB without the column gains it idempotently."""
    import sqlite3
    db = isolated / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, op TEXT NOT NULL, "
        "payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', "
        "attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL "
        "DEFAULT 3, priority INTEGER NOT NULL DEFAULT 100, result TEXT, "
        "error TEXT, worker_id TEXT, created_at REAL NOT NULL, started_at REAL, "
        "finished_at REAL)"
    )
    conn.commit()
    conn.close()
    q = SqliteJobQueue(db)  # migration runs in __init__
    jid = q.enqueue("batch_align", {"project_id": "p", "docs": []})
    q.set_progress(jid, 2, 2)
    assert q.get(jid).progress == {"done": 2, "total": 2}


# -- (b) op-level: on_cluster_done fires monotonically up to total -----------

def test_on_cluster_done_monotonic_to_total():
    """A >=2-cluster batch's callback reports cumulative non-decreasing counts
    whose last value equals the number of files."""
    schema = _schema()
    # two distinct shapes -> two clusters; junk is a third cluster.
    docs = [
        SourceDoc(doc_id="a", records=[{"name": "A", "releaseYear": "2020"}]),
        SourceDoc(doc_id="b", records=[{"name": "B", "releaseYear": "2021"}]),
        SourceDoc(doc_id="j", records=[{"weather": "sunny"}]),
    ]
    total = len(docs)
    seen: list[int] = []
    run_batch(docs, schema, ProfileLibrary(), reject_below=0.5,
              research_available=False,
              on_cluster_done=lambda done: seen.append(done))
    assert seen, "callback never fired"
    assert seen == sorted(seen), "callback values must be non-decreasing"
    assert all(0 < v <= total for v in seen)
    assert seen[-1] == total


def test_on_cluster_done_default_none_is_observational():
    """run_batch with on_cluster_done=None produces identical outcomes."""
    schema = _schema()
    docs = [
        SourceDoc(doc_id="a", records=[{"name": "A", "releaseYear": "2020"}]),
        SourceDoc(doc_id="b", records=[{"name": "B", "releaseYear": "2021"}]),
    ]
    base = run_batch(docs, schema, ProfileLibrary(), reject_below=0.5,
                     research_available=False)
    with_cb = run_batch(docs, schema, ProfileLibrary(), reject_below=0.5,
                        research_available=False,
                        on_cluster_done=lambda done: None)
    assert base.to_dict() == with_cb.to_dict()


# -- (c) API: completed job keeps the exact pre-change shape, no progress ----

def test_completed_job_status_has_no_progress_key(isolated):
    from app.jobs.runtime import get_queue
    from app.main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Prog"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema",
               json={"target": _schema()})

    shape = {"name": "Z", "releaseYear": "2020", "developer": "D",
             "genres": ["X"]}
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": [{"doc_id": "a", "records": [shape]},
                                   {"doc_id": "b", "records": [shape]}],
                          "research": True})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    Worker(get_queue(), "w1").run_once()
    status = client.get(f"/jobs/{job_id}").json()

    assert status["state"] == "completed"
    assert "progress" not in status
    assert set(status["result"].keys()) == {"ok", "summary", "outcomes", "report"}
