"""API + service tests for the Reject lifecycle and the free-form vocab actions.

Reject validates server-side (hollow reasons -> 422, nothing persisted), moves
the shape under `rejected`, and is reversible via /restore to its pre-reject
state. The value-vocab endpoint lists every schema field with a free_form flag,
and set/clear_free_form thread end-to-end to the store.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import settings
from app.jobs import Worker
from app.jobs import ops as _ops  # noqa: F401  registers ops

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return tmp_path


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


_SHAPE = {"name": "Z", "releaseYear": "2020", "developer": "D", "genres": ["X"]}


def _client():
    from app.main import app
    from starlette.testclient import TestClient
    return TestClient(app)


def _project_with_provisional(client) -> tuple[str, str]:
    """Create a project, set the schema, run a batch that mints a provisional
    profile, and return (project_id, provisional_profile_id)."""
    from app.jobs.runtime import get_queue
    pid = client.post("/projects", json={"name": "Rej"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": [{"doc_id": "a", "records": [_SHAPE]}],
                          "research": True})
    Worker(get_queue(), "w1").run_once()
    assert client.get(f"/jobs/{r.json()['job_id']}").json()["result"]["ok"]
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert lib["provisional"], "expected a provisional profile"
    return pid, lib["provisional"][0]


# -- reject validation -------------------------------------------------------

def test_reject_valid_moves_shape_to_rejected(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/reject",
                    json={"profile_id": prof, "category": "not_our_data"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "rejected"
    assert body["rejection"]["composed_text"] == "Rejected — not our data."
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert prof in lib["rejected"]
    assert prof not in lib["provisional"]
    entry = next(e for e in lib["entries"] if e["profile_id"] == prof)
    assert entry["rejection"]["category"] == "not_our_data"


def test_reject_other_trivial_note_is_422_and_not_persisted(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/reject",
                    json={"profile_id": prof, "category": "other", "note": "no"})
    assert r.status_code == 422
    assert "informative note" in r.json()["detail"]
    # nothing persisted: still provisional
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert prof in lib["provisional"]
    assert prof not in lib["rejected"]


def test_reject_wrong_mapping_bad_field_is_422(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/reject",
                    json={"profile_id": prof, "category": "wrong_mapping",
                          "fields": ["totally_made_up"]})
    assert r.status_code == 422
    assert "not a golden field" in r.json()["detail"]


def test_reject_unknown_profile_is_404(isolated):
    client = _client()
    pid, _ = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/reject",
                    json={"profile_id": "nope", "category": "not_our_data"})
    assert r.status_code == 404


# -- restore -----------------------------------------------------------------

def test_restore_provisional_round_trip(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    client.post(f"/projects/{pid}/alignment/reject",
                json={"profile_id": prof, "category": "not_our_data"})
    r = client.post(f"/projects/{pid}/alignment/restore", json={"profile_id": prof})
    assert r.status_code == 200 and r.json()["state"] == "provisional"
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert prof in lib["provisional"] and prof not in lib["rejected"]


def test_approve_then_reject_then_restore_returns_to_approved(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    client.post(f"/projects/{pid}/alignment/approve", json={"profile_id": prof})
    client.post(f"/projects/{pid}/alignment/reject",
                json={"profile_id": prof, "category": "not_our_data"})
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    # approved shape disappears from approved while rejected
    assert prof not in lib["approved"] and prof in lib["rejected"]
    r = client.post(f"/projects/{pid}/alignment/restore", json={"profile_id": prof})
    assert r.json()["state"] == "approved"
    lib2 = client.get(f"/projects/{pid}/alignment/library").json()
    assert prof in lib2["approved"]


def test_restore_non_rejected_is_409(isolated):
    client = _client()
    pid, prof = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/restore", json={"profile_id": prof})
    assert r.status_code == 409


def test_restore_unknown_is_404(isolated):
    client = _client()
    pid, _ = _project_with_provisional(client)
    r = client.post(f"/projects/{pid}/alignment/restore", json={"profile_id": "nope"})
    assert r.status_code == 404


# -- full workflow: reject -> rerun declined -> restore -> rerun provisional --

def test_full_reject_rerun_restore_workflow(isolated):
    from app.jobs.runtime import get_queue
    client = _client()
    pid, prof = _project_with_provisional(client)
    client.post(f"/projects/{pid}/alignment/reject",
                json={"profile_id": prof, "category": "duplicate_shape"})

    # rerun same shape -> declined, no new provisional card
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": [{"doc_id": "b", "records": [_SHAPE]}],
                          "research": True})
    Worker(get_queue(), "w1").run_once()
    s = client.get(f"/jobs/{r.json()['job_id']}").json()["result"]["summary"]
    assert s["doc_counts_by_pathway"].get("declined", 0) == 1
    lib = client.get(f"/projects/{pid}/alignment/library").json()
    assert lib["provisional"] == []

    # restore -> rerun -> learnable again (provisional)
    client.post(f"/projects/{pid}/alignment/restore", json={"profile_id": prof})
    r2 = client.post(f"/projects/{pid}/alignment/batch",
                     json={"docs": [{"doc_id": "c", "records": [_SHAPE]}],
                           "research": True})
    Worker(get_queue(), "w1").run_once()
    assert client.get(f"/jobs/{r2.json()['job_id']}").json()["result"]["ok"]
    lib2 = client.get(f"/projects/{pid}/alignment/library").json()
    assert prof in lib2["provisional"]


# -- free-form vocab actions -------------------------------------------------

def test_value_vocab_lists_all_fields_with_free_form(isolated):
    client = _client()
    pid = client.post("/projects", json={"name": "V"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    vocab = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    targets = {f["target"] for f in vocab["fields"]}
    # every schema field appears, including free-text ones with no taxonomy
    assert {"name", "developer", "platform", "genres", "ESRB"} <= targets
    for f in vocab["fields"]:
        assert f["free_form"] is False


def test_set_and_clear_free_form_threads_end_to_end(isolated):
    client = _client()
    pid = client.post("/projects", json={"name": "V2"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})

    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "set_free_form", "target": "platform",
                          "free_form": True})
    assert r.status_code == 200
    assert r.json()["change"]["entry"]["free_form"] is True
    vocab = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    plat = next(f for f in vocab["fields"] if f["target"] == "platform")
    assert plat["free_form"] is True

    r2 = client.post(f"/projects/{pid}/alignment/value-vocab",
                     json={"action": "clear_free_form", "target": "platform"})
    assert r2.json()["change"]["entry"]["free_form"] is False
    vocab2 = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    plat2 = next(f for f in vocab2["fields"] if f["target"] == "platform")
    assert plat2["free_form"] is False


def test_set_free_form_on_non_field_is_400(isolated):
    client = _client()
    pid = client.post("/projects", json={"name": "V3"}).json()["id"]
    client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "set_free_form", "target": "bogus",
                          "free_form": True})
    assert r.status_code == 400
