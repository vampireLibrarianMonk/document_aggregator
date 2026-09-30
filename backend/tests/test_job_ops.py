"""Real worker ops: the async path runs the same pipeline as the sync API."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.jobs import SqliteJobQueue, Worker  # noqa: E402
from app.jobs import ops as _ops  # noqa: E402,F401  registers ops
from app.jobs.worker import registered_ops  # noqa: E402


def test_real_ops_registered():
    for op in ("reconcile", "converge", "render_geometry", "pipeline"):
        assert op in registered_ops()


def test_reconcile_op_via_worker(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("reconcile", {"scenario_id": "1", "mode": "draft"})
    assert Worker(q, "w1").run_once() is True
    job = q.get(jid)
    assert job.state.value == "completed"
    assert "summary" in job.result
    assert job.result["summary"]["total_units"] > 0


def test_pipeline_op_from_docx_source(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("pipeline", {"scenario_id": "1", "mode": "draft", "source_format": "docx"})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value == "completed"
    assert job.result["discipline_finding_count"] >= 1  # inspection ran
    # vector tier flag is present (True only if LibreOffice available)
    assert "vector_tier_ran" in job.result


def test_render_geometry_op_degrades_without_soffice(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("render_geometry", {"scenario_id": "1", "mode": "draft", "source_format": "docx"})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value == "completed"
    # Either it ran (soffice present) or degraded cleanly (findings empty, reason set).
    assert "ran" in job.result
    if not job.result["ran"]:
        assert job.result["findings"] == []
        assert job.result["reason"]
