"""The app starts EMPTY and the REAL user flow is upload -> correct.

The in-app samples feature was removed: there is no templates catalog and no
one-click instantiation. A user creates an empty project and UPLOADS the project
scenario folder's files; the ingestion->correction bridge persists them into the
correction-data store so the Correction Pipeline reconciles genuine uploads.

These tests assert that real flow end to end (offline, by feeding the committed
sample_docs/project/1 fixture bytes through the bridge exactly as an upload
would), plus the empty-start model and the project-delete safety guarantees.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from app import correction_bridge
from app import project as sc
from app.config import settings
from app.models import Project
from app.store import Store

# The committed scenario folder the TGX-9 guide points users at.
_CASE_1 = settings.BUNDLED_PROJECT_ROOT / "1"


@pytest.fixture
def clean_store(tmp_path, monkeypatch) -> Store:
    """A Store backed by an empty, isolated DATA_DIR so nothing leaks between
    tests and the app genuinely starts with zero projects."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    return Store()


def _upload_case_1(store: Store) -> str:
    """Create an empty project and upload every TGX-9 scenario file through the
    bridge, exactly as the real upload endpoint does (kind + filename + bytes).
    Returns the new project id."""
    proj = store.create_project(Project(id="proj_uploadtest", name="TGX-9 upload"))
    pid = proj.id

    def up(rel: str, kind: str) -> None:
        p = _CASE_1 / rel
        correction_bridge.bridge_upload(pid, kind, p.name, p.read_bytes())

    up("project.json", "corpus")  # the manifest (any area; routed by filename)
    up("corpus/field_report_2026-03-02.txt", "corpus")
    up("corpus/root_cause_notes_2026-03-15.md", "corpus")
    up("corpus/graphics.json", "corpus")
    up("template/incident_report_template.json", "template")
    up("first_attempt/incident_report_draft.json", "first_draft")
    up("corrections/comments.json", "corrections")
    return pid


def test_app_starts_empty(clean_store):
    assert clean_store.list_projects() == []


def test_upload_populates_the_correction_store(clean_store):
    """Uploading the scenario files makes the correction store genuinely ready
    (the honest readiness check the UI gates on)."""
    pid = _upload_case_1(clean_store)
    state = correction_bridge.correction_store_ready(pid)
    assert state["ready"], state["problems"]
    assert state["has_manifest"]
    assert state["has_corpus"]
    assert state["has_template"]
    assert state["has_first_draft"]
    assert state["has_corrections"]


def test_uploaded_project_shows_first_attempt_component(clean_store):
    """component_overview must see the bridged first_attempt (what the
    Correction Pipeline tab checks before it renders)."""
    pid = _upload_case_1(clean_store)
    comps = {c["id"]: c for c in sc.component_overview(pid)}
    assert len(comps["first_attempt"]["items"]) > 0
    assert len(comps["corpus"]["items"]) > 0


def test_uploaded_project_reconciles_to_the_guide_numbers(clean_store):
    """The whole point: a project built purely by upload reconciles to the exact
    values the TGX-9 guide promises (draft 17 = 3/4/8/1/1; template 16 =
    0/11/3/1/1), with the severity conflict preserved."""
    pid = _upload_case_1(clean_store)

    def counts(summary: dict) -> tuple[int, int, int, int, int]:
        # A status key is omitted from the summary when its count is 0.
        return (
            summary.get("unchanged", 0), summary.get("filled", 0),
            summary.get("corrected", 0), summary.get("needs_review", 0),
            summary.get("conflict", 0),
        )

    draft = sc.run_reconciliation("draft", pid, "json")["summary"]
    assert draft["total_units"] == 17
    assert counts(draft) == (3, 4, 8, 1, 1)

    template = sc.run_reconciliation("template", pid, "json")["summary"]
    assert template["total_units"] == 16
    assert counts(template) == (0, 11, 3, 1, 1)


def test_delete_project_removes_it(clean_store):
    pid = _upload_case_1(clean_store)
    assert pid in {p.id for p in clean_store.list_projects()}
    assert clean_store.delete_project(pid) is True
    assert pid not in {p.id for p in clean_store.list_projects()}
    # Deleting again is a no-op (already gone).
    assert clean_store.delete_project(pid) is False


def test_delete_unknown_project_is_false(clean_store):
    assert clean_store.delete_project("proj_doesnotexist") is False


def test_delete_never_touches_the_committed_scenario_folders(clean_store):
    """A bundled scenario-folder id must NOT be deletable via the store (the
    store only ever removes directories under its own projects root)."""
    assert (_CASE_1 / "project.json").exists()
    assert clean_store.delete_project("1") is False
    assert (_CASE_1 / "project.json").exists()  # scenario folder untouched


def test_upload_leaves_the_scenario_folder_untouched(clean_store):
    """Uploading reads the committed fixtures; it must never modify them."""
    before = sorted(p.name for p in _CASE_1.iterdir())
    _upload_case_1(clean_store)
    after = sorted(p.name for p in _CASE_1.iterdir())
    assert before == after
    assert (_CASE_1 / "project.json").exists()


def test_each_project_is_independent(clean_store):
    a = clean_store.create_project(Project(id="proj_a", name="A"))
    b = clean_store.create_project(Project(id="proj_b", name="B"))
    assert a.id != b.id
    assert Path(settings.project_data_dir(a.id)) != Path(settings.project_data_dir(b.id))
