"""Job queue + worker alpha loop with FAUX ops.

Proves the dispatch/queue mechanics before real (LibreOffice) ops are attached:
  - atomic claim: N concurrent workers never double-process a job;
  - every enqueued job is processed exactly once;
  - failures become retryable then failed after max_attempts;
  - results are recorded and retrievable.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.jobs import SqliteJobQueue, Worker, register_op  # noqa: E402


@register_op("faux_echo")
def _faux_echo(payload: dict) -> dict:
    return {"echo": payload.get("n")}


@register_op("faux_boom")
def _faux_boom(payload: dict) -> dict:
    raise RuntimeError("boom")


def test_single_job_roundtrip(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("faux_echo", {"n": 7})
    w = Worker(q, "w1")
    assert w.run_once() is True
    job = q.get(jid)
    assert job.state.value == "completed"
    assert job.result == {"echo": 7}


def test_no_double_processing_under_concurrency(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    n = 200
    for i in range(n):
        q.enqueue("faux_echo", {"n": i})

    processed_counts = []

    def run_worker(wid: str):
        w = Worker(q, wid, idle_sleep=0.001)
        w.run_forever(max_idle_cycles=5)
        processed_counts.append(w.processed)

    threads = [threading.Thread(target=run_worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = q.counts()
    assert counts.get("completed", 0) == n, f"expected {n} completed, got {counts}"
    # Total processed across workers equals n exactly -> no double-processing.
    assert sum(processed_counts) == n
    # Work was actually shared across more than one worker.
    assert sum(1 for c in processed_counts if c > 0) >= 2


def test_failure_retries_then_fails(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("faux_boom", {}, max_attempts=3)
    w = Worker(q, "w1")
    # Attempt 1 -> retryable, 2 -> retryable, 3 -> failed.
    for _ in range(3):
        w.run_once()
    job = q.get(jid)
    assert job.state.value == "failed"
    assert job.attempts == 3
    assert "boom" in (job.error or "")


def test_unknown_op_fails_gracefully(tmp_path):
    q = SqliteJobQueue(tmp_path / "j.db")
    jid = q.enqueue("does_not_exist", {})
    Worker(q, "w1").run_once()
    job = q.get(jid)
    assert job.state.value in ("failed", "retryable")
    assert "no registered op" in (job.error or "")
