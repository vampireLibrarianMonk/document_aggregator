"""Tests for the per-project reject_below dial (Phase A persistence + API).

The dial is stored on the Project record, defaults to 0.5, clamps to [0,1], and
is editable via PATCH /projects/{id}. Existing project.json without the field
round-trips to the default (no migration needed).
"""
from __future__ import annotations

import pytest
from app.config import settings
from app.models import Project
from app.store import Store


@pytest.fixture
def clean_store(tmp_path, monkeypatch) -> Store:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    return Store()


def test_default_reject_below_is_half():
    assert Project(id="p1", name="x").reject_below == 0.5


def test_reject_below_is_clamped():
    assert Project(id="p1", name="x", reject_below=5.0).reject_below == 1.0
    assert Project(id="p2", name="x", reject_below=-2.0).reject_below == 0.0
    assert Project(id="p3", name="x", reject_below=0.3).reject_below == 0.3


def test_legacy_project_json_without_field_defaults(clean_store):
    # Simulate an older record missing reject_below -> Project(**data) defaults it.
    legacy = {"id": "old", "name": "Old", "description": "", "created_at": "2026-01-01"}
    proj = Project(**legacy)
    assert proj.reject_below == 0.5


def test_store_roundtrips_reject_below(clean_store):
    clean_store.create_project(Project(id="p1", name="x", reject_below=0.7))
    got = clean_store.get_project("p1")
    assert got is not None and got.reject_below == 0.7
    # update it
    got.reject_below = 0.25
    clean_store.update_project(got)
    assert clean_store.get_project("p1").reject_below == 0.25


def test_patch_endpoint_updates_dial(clean_store, monkeypatch):
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)

    created = client.post("/projects", json={"name": "Batch proj"}).json()
    pid = created["id"]
    assert created["reject_below"] == 0.5

    r = client.patch(f"/projects/{pid}", json={"reject_below": 0.8})
    assert r.status_code == 200
    assert r.json()["reject_below"] == 0.8

    # persisted
    assert client.get(f"/projects/{pid}").json()["reject_below"] == 0.8

    # clamp through the API too
    r2 = client.patch(f"/projects/{pid}", json={"reject_below": 2.0})
    assert r2.json()["reject_below"] == 1.0

    # partial update leaves other fields intact
    r3 = client.patch(f"/projects/{pid}", json={"name": "Renamed"})
    body = r3.json()
    assert body["name"] == "Renamed" and body["reject_below"] == 1.0
