"""The app starts EMPTY and the bundled sample cases are TEMPLATES.

These assert the empty-start model: the live project list is empty until the
user creates something, the templates catalog surfaces the bundled cases, and
instantiating a template copies it into the store as a new persisted project
(leaving the repo fixture untouched) that then loads + reconciles.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from app import project as sc
from app.config import settings
from app.store import Store


@pytest.fixture
def clean_store(tmp_path, monkeypatch) -> Store:
    """A Store backed by an empty, isolated DATA_DIR so nothing leaks between
    tests and the app genuinely starts with zero projects."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    return Store()


def test_app_starts_empty(clean_store):
    assert clean_store.list_projects() == []


def test_templates_catalog_lists_bundled_cases(clean_store):
    # The bundle is still readable as a template catalog even on an empty store.
    cases = sc.list_project_cases()
    ids = {c["id"] for c in cases}
    assert {"1", "2", "3", "4", "5", "6"}.issubset(ids)
    for c in cases:
        assert c["title"]


def test_instantiate_creates_a_persisted_project(clean_store):
    proj = clean_store.instantiate_from_template("1")
    assert proj.id.startswith("proj_")
    assert proj.name  # carried from the case manifest title
    # It is now a real, listed project.
    listed = {p.id for p in clean_store.list_projects()}
    assert proj.id in listed
    # Its correction data resolves to the STORE copy, not the bundle.
    data_dir = settings.project_data_dir(proj.id)
    assert (data_dir / "project.json").exists()
    assert (data_dir / "first_attempt").is_dir()


def test_instantiated_project_reconciles(clean_store):
    proj = clean_store.instantiate_from_template("1")
    report = sc.run_reconciliation("draft", proj.id, "json")
    assert report["summary"]["total_units"] > 0


def test_instantiate_is_a_copy_not_a_move(clean_store):
    # The repo fixture must be untouched after instantiation.
    src = settings.template_dir("1")
    before = sorted(p.name for p in src.iterdir())
    clean_store.instantiate_from_template("1")
    after = sorted(p.name for p in src.iterdir())
    assert before == after
    assert (src / "project.json").exists()


def test_instantiate_unknown_case_raises(clean_store):
    with pytest.raises(FileNotFoundError):
        clean_store.instantiate_from_template("does-not-exist")


def test_each_instantiation_is_independent(clean_store):
    a = clean_store.instantiate_from_template("1")
    b = clean_store.instantiate_from_template("1")
    assert a.id != b.id
    ids = {p.id for p in clean_store.list_projects()}
    assert {a.id, b.id}.issubset(ids)
    # Writing into one must not affect the other (separate data dirs).
    assert Path(settings.project_data_dir(a.id)) != Path(settings.project_data_dir(b.id))
