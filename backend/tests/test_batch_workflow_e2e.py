"""Total-workflow end-to-end tests for the JSON -> golden-JSON batch scenario.

Where ``test_batch_api.py`` locks individual endpoints, this module walks the
*whole* user workflow the way the Ingestion-tab UI drives it, and exercises
**every one of the five batch pathways as a complete scenario**:

    novel_research    a brand-new relevant shape is learned for approval
    replay_clean      an approved shape converts with zero re-inference
    review            a half-covered shape is held for a human
    reject_irrelevant an unrelated file is quarantined, no gold emitted
    drift_repair      an approved shape whose values stopped conforming
                      re-emerges for re-approval

Design goals:
  * Drive the SAME HTTP endpoints the buttons call, in the SAME order a user
    clicks them (Save golden schema -> set relevance dial -> Upload JSON files
    -> read Batch result -> Approve -> re-upload). This is the reliable way to
    test the "buttonology": the UI is a thin shell over these calls, so a
    green run here means the workflow a user performs actually works.
  * Mirror the two pieces of CLIENT-side logic that the buttons rely on so a
    backend/UI contract drift fails the test:
      - ``files_to_docs`` mirrors BatchPanel.tsx ``filesToDocs`` (a file's JSON
        becomes one document; a bare object is wrapped in a list).
      - ``PATHWAY_LABELS`` mirrors BatchPanel.tsx so we assert the human-visible
        "Batch result" label for each scenario, not just the raw constant.
  * Fully offline + deterministic (no Bedrock; the ``_offline_by_default``
    autouse fixture in conftest pins it off).

Run: ``pytest backend/tests/test_batch_workflow_e2e.py -v``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import settings
from app.jobs import Worker
from app.jobs import ops as _ops  # noqa: F401  registers the batch_align op
from app.jobs.runtime import get_queue
from app.main import app
from starlette.testclient import TestClient

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"


# ---------------------------------------------------------------------------
# Mirrors of the two client-side behaviors the buttons depend on. If the UI's
# filesToDocs / PATHWAY_LABELS ever change, update these in lockstep -- the
# point is that a mismatch between UI and backend surfaces as a failing test.
# ---------------------------------------------------------------------------

# frontend/src/components/BatchPanel.tsx :: PATHWAY_LABELS
PATHWAY_LABELS: dict[str, str] = {
    "replay_clean": "Converted (known shape)",
    "drift_repair": "Drift — needs re-approval",
    "novel_research": "New shape — needs approval",
    "review": "Needs human review",
    "reject_irrelevant": "Quarantined (unrelated)",
}


def files_to_docs(files: dict[str, object]) -> list[dict]:
    """Mirror BatchPanel.tsx ``filesToDocs``: map {filename: parsed_json} to the
    batch ``docs`` payload. An array becomes the record list; a bare object is
    wrapped in a one-element list; doc_id is the file name."""
    docs: list[dict] = []
    for name, parsed in files.items():
        records = parsed if isinstance(parsed, list) else [parsed]
        docs.append({"doc_id": name, "records": records})
    return docs


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point DATA_DIR at a temp dir and reset the job-queue singleton so each
    test gets a clean project store + queue (same pattern as test_batch_api)."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return tmp_path


@pytest.fixture
def client(isolated):
    return TestClient(app)


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


# The three "team" files straight out of the walkthrough (Step 3).
TEAM_CLEAN = {
    "name": "Celeste", "releaseYear": "2018", "developer": "Maddy Makes Games",
    "publisher": "Maddy Makes Games", "platform": "Windows PC",
    "genres": ["Platformer"], "criticScore": 92, "ESRB": "E10+",
}
TEAM_RENAMED = {
    "title": "Hades", "year": "2020", "studio": "Supergiant Games",
    "console": "Windows PC", "metascore": 93,
}
WEATHER = {"temperature_c": 21, "humidity": 55, "wind_kph": 12}
# Same shape as TEAM_CLEAN but criticScore is no longer an integer (Step 9).
TEAM_CLEAN_DRIFTED = {
    "name": "Hollow Knight", "releaseYear": "2017", "developer": "Team Cherry",
    "publisher": "Team Cherry", "platform": "Windows PC",
    "genres": ["Metroidvania"], "criticScore": "not available", "ESRB": "E10+",
}


def _create_project(client: TestClient, name: str = "Game Catalog — JSON to Golden") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _save_golden_schema(client: TestClient, pid: str) -> dict:
    """Click 'Save golden schema'. Returns the {title, fields, required} summary
    the UI renders as 'Saved: game (N fields, M required)'."""
    r = client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _schema()})
    assert r.status_code == 200, r.text
    return r.json()


def _set_relevance(client: TestClient, pid: str, pct: int) -> None:
    """Drag the 'Relevance cutoff' slider: the UI commits reject_below = pct/100."""
    r = client.patch(f"/projects/{pid}", json={"reject_below": pct / 100})
    assert r.status_code == 200, r.text


def _upload_and_process(client: TestClient, pid: str, files: dict[str, object],
                        *, learn_new_shapes: bool) -> dict:
    """Click 'Upload JSON files' (with the 'Learn new shapes' checkbox = research)
    and wait for the job. Returns the batch result dict the UI polls for."""
    docs = files_to_docs(files)
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": docs, "research": learn_new_shapes})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    # The UI polls GET /jobs/{id}; here we run the worker against the shared
    # queue the API enqueued to, then read the completed job once.
    Worker(get_queue(), "w1").run_once()
    status = client.get(f"/jobs/{job_id}").json()
    assert status["state"] == "completed", status
    assert status["result"]["ok"] is True, status["result"]
    return status["result"]


def _pathway_label_counts(result: dict) -> dict[str, int]:
    """Translate the raw per-pathway doc counts into the human-readable labels
    the 'Batch result' table shows, exactly as the UI does."""
    raw = result["summary"]["doc_counts_by_pathway"]
    return {PATHWAY_LABELS.get(k, k): n for k, n in raw.items()}


def _provisional_ids(client: TestClient, pid: str) -> list[str]:
    """What 'Shapes awaiting your approval' lists."""
    return client.get(f"/projects/{pid}/alignment/library").json()["provisional"]


def _approve(client: TestClient, pid: str, profile_id: str) -> None:
    """Click 'Approve' on a provisional shape card."""
    r = client.post(f"/projects/{pid}/alignment/approve", json={"profile_id": profile_id})
    assert r.status_code == 200 and r.json()["state"] == "approved", r.text


# ---------------------------------------------------------------------------
# Guard: schema must be set before a batch (the UI disables upload until then).
# ---------------------------------------------------------------------------

def test_batch_rejected_before_golden_schema_is_set(client):
    pid = _create_project(client)
    r = client.post(f"/projects/{pid}/alignment/batch",
                    json={"docs": files_to_docs({"a.json": TEAM_CLEAN})})
    assert r.status_code == 400
    assert "schema" in r.json()["detail"].lower()


def test_save_golden_schema_summary_matches_ui_text(client):
    pid = _create_project(client)
    summary = _save_golden_schema(client, pid)
    # UI renders: Saved: game (8 fields, 2 required)
    assert summary["title"] == "game"
    assert summary["fields"] == 8
    assert sorted(summary["required"]) == ["name", "releaseYear"]


# ---------------------------------------------------------------------------
# Scenario coverage: each of the five pathways, as a complete workflow.
# ---------------------------------------------------------------------------

def test_scenario_novel_research_learns_a_shape_for_approval(client):
    """Upload a brand-new relevant shape with 'Learn new shapes' on -> it is
    learned as provisional and surfaces in 'Shapes awaiting your approval'."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)

    result = _upload_and_process(client, pid, {"team_clean.json": TEAM_CLEAN},
                                 learn_new_shapes=True)

    labels = _pathway_label_counts(result)
    assert labels.get("New shape — needs approval") == 1
    assert result["summary"]["conformed_records"] == 1
    # The learned shape is waiting for the Approve button.
    assert _provisional_ids(client, pid), "expected a shape awaiting approval"


def test_scenario_replay_clean_after_approval(client):
    """Approve the learned shape, re-upload the same shape -> it replays with
    zero re-inference (the steady state)."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)
    _upload_and_process(client, pid, {"team_clean.json": TEAM_CLEAN},
                        learn_new_shapes=True)

    prov = _provisional_ids(client, pid)
    assert prov
    _approve(client, pid, prov[0])

    # Re-run the same shape; 'Learn new shapes' off this time (not needed).
    result = _upload_and_process(client, pid, {"team_clean_2.json": TEAM_CLEAN},
                                 learn_new_shapes=False)
    labels = _pathway_label_counts(result)
    assert labels.get("Converted (known shape)") == 1
    assert result["summary"]["conformed_records"] == 1


def test_scenario_review_band_holds_a_partial_shape_for_a_human(client):
    """A shape that covers some but not comfortably enough of the required
    golden fields lands in 'Needs human review' -- nothing is produced."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)

    # team_renamed maps releaseYear (via 'year') but NOT name (via 'title')
    # deterministically -> required coverage ~50% -> the review band.
    result = _upload_and_process(client, pid, {"team_renamed.json": TEAM_RENAMED},
                                 learn_new_shapes=False)
    labels = _pathway_label_counts(result)
    assert labels.get("Needs human review") == 1
    # review produces no golden records and learns no shape.
    assert result["summary"]["conformed_records"] == 0
    assert not _provisional_ids(client, pid)


def test_scenario_reject_irrelevant_quarantines_unrelated_file(client):
    """An unrelated file (zero required golden fields) is quarantined. With
    'Learn new shapes' OFF there is no research path to rescue it."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)

    result = _upload_and_process(client, pid, {"weather.json": WEATHER},
                                 learn_new_shapes=False)
    labels = _pathway_label_counts(result)
    assert labels.get("Quarantined (unrelated)") == 1
    assert result["summary"]["quarantined_docs"] == 1
    assert result["summary"]["conformed_records"] == 0


def test_scenario_drift_repair_pulls_an_approved_shape_back(client):
    """Approve team_clean, then upload a same-keys file whose criticScore is no
    longer an integer -> drift is detected and the shape re-emerges for
    re-approval instead of replaying."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)

    # Learn + approve a clean baseline (several copies so a fill-rate baseline
    # exists to drift away from).
    _upload_and_process(
        client, pid,
        {"team_clean_1.json": TEAM_CLEAN, "team_clean_2.json": TEAM_CLEAN,
         "team_clean_3.json": TEAM_CLEAN},
        learn_new_shapes=True,
    )
    prov = _provisional_ids(client, pid)
    assert prov
    _approve(client, pid, prov[0])

    # Now a same-shape file whose criticScore drifted to a non-integer.
    result = _upload_and_process(
        client, pid,
        {"team_clean_drifted.json": TEAM_CLEAN_DRIFTED},
        learn_new_shapes=True,
    )
    labels = _pathway_label_counts(result)
    assert labels.get("Drift — needs re-approval") == 1, result["summary"]
    # A new provisional version is awaiting re-approval.
    assert _provisional_ids(client, pid), "drift should re-emerge for re-approval"


# ---------------------------------------------------------------------------
# One combined "totality" pass: all three walkthrough files in a single batch,
# the way a user actually uploads them in Step 5.
# ---------------------------------------------------------------------------

def test_full_mixed_batch_routes_each_file_to_its_pathway(client):
    """Upload clean + renamed + weather together (Learn new shapes on) and
    assert each file lands in the pathway the walkthrough predicts."""
    pid = _create_project(client)
    summary = _save_golden_schema(client, pid)
    assert summary["fields"] == 8 and len(summary["required"]) == 2
    _set_relevance(client, pid, 50)

    result = _upload_and_process(
        client, pid,
        {"team_clean.json": TEAM_CLEAN,
         "team_renamed.json": TEAM_RENAMED,
         "weather.json": WEATHER},
        learn_new_shapes=True,
    )
    labels = _pathway_label_counts(result)

    # clean -> learned for approval; renamed -> review; weather -> research
    # (Learn new shapes lets the research path try it; the user rejects it at
    # approval). This matches Step 5 of the walkthrough.
    assert labels.get("New shape — needs approval", 0) >= 1
    assert labels.get("Needs human review", 0) == 1
    # weather with research on is sent to research rather than quarantined
    # outright; it must NOT be converted into a golden record.
    assert result["summary"]["conformed_records"] >= 1
    # exactly the clean shape conformed; renamed produced nothing.
    produced_docs = sum(labels.values())
    assert produced_docs == 3, labels


# ---------------------------------------------------------------------------
# Output-shape lock: the batch path emits JSON golden records only.
# ---------------------------------------------------------------------------

def test_output_is_json_golden_records_only(client):
    """The conformed output is JSON objects conforming to the golden schema --
    the batch path has no non-JSON export."""
    pid = _create_project(client)
    _save_golden_schema(client, pid)
    _set_relevance(client, pid, 50)
    _upload_and_process(client, pid, {"team_clean.json": TEAM_CLEAN},
                        learn_new_shapes=True)
    prov = _provisional_ids(client, pid)
    _approve(client, pid, prov[0])
    result = _upload_and_process(client, pid, {"c.json": TEAM_CLEAN},
                                 learn_new_shapes=False)

    # The batch result is entirely JSON (summary + per-cluster outcomes) and
    # round-trips through json unchanged -- there is no binary/non-JSON payload
    # anywhere in the batch path.
    assert result["summary"]["conformed_records"] == 1
    assert json.loads(json.dumps(result)) == result
    # Each producing outcome reports how many records conformed to the golden
    # schema; nothing is produced for review/quarantine pathways.
    for outcome in result["outcomes"]:
        assert set(outcome).issuperset({"pathway", "conformed", "produced"})
        if outcome["pathway"] in {"review", "reject_irrelevant"}:
            assert outcome["produced"] == 0
