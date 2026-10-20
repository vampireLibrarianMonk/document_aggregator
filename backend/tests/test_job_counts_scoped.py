"""Tests for project-scoped job-queue tallies (`counts_for_project`) and the
`GET /jobs?project_id=` endpoint.

The Workers & queue panel must show the ACTIVE project's work only — a lifetime
global tally bleeds every other project's jobs into the one you're viewing. The
queue has no project_id column; scoping filters on the project_id carried in
each job's payload JSON.
"""
from __future__ import annotations

import pytest
from app.config import settings
from app.jobs import SqliteJobQueue


@pytest.fixture
def queue(tmp_path) -> SqliteJobQueue:
    return SqliteJobQueue(tmp_path / "jobs.db")


def _complete(q: SqliteJobQueue, op: str, project_id: str) -> str:
    jid = q.enqueue(op, {"project_id": project_id, "docs": []})
    q.complete(jid, {"ok": True})
    return jid


def test_counts_for_project_isolates_each_project(queue):
    # Two jobs for A, one for B, all completed.
    _complete(queue, "batch_align", "proj_A")
    _complete(queue, "batch_align", "proj_A")
    _complete(queue, "batch_align", "proj_B")

    assert queue.counts() == {"completed": 3}                 # global unchanged
    assert queue.counts_for_project("proj_A") == {"completed": 2}
    assert queue.counts_for_project("proj_B") == {"completed": 1}
    assert queue.counts_for_project("proj_unknown") == {}      # nothing bleeds in


def test_counts_for_project_separates_states(queue):
    # One pending + one completed for A; one completed for B.
    queue.enqueue("batch_align", {"project_id": "proj_A"})     # stays pending
    _complete(queue, "batch_align", "proj_A")
    _complete(queue, "batch_align", "proj_B")

    a = queue.counts_for_project("proj_A")
    assert a == {"pending": 1, "completed": 1}
    assert queue.counts_for_project("proj_B") == {"completed": 1}


def test_jobs_endpoint_scopes_when_project_id_given(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)

    from app.main import app
    from fastapi.testclient import TestClient

    # Seed two projects' jobs through the SAME queue the endpoint reads.
    q = rt.get_queue()
    _complete(q, "batch_align", "proj_A")
    _complete(q, "batch_align", "proj_A")
    _complete(q, "batch_align", "proj_B")

    client = TestClient(app)
    # Global (no param): all three.
    assert client.get("/jobs").json()["counts"] == {"completed": 3}
    # Scoped: only that project's.
    assert client.get("/jobs", params={"project_id": "proj_A"}).json()["counts"] == {"completed": 2}
    assert client.get("/jobs", params={"project_id": "proj_B"}).json()["counts"] == {"completed": 1}
    assert client.get("/jobs", params={"project_id": "nope"}).json()["counts"] == {}
