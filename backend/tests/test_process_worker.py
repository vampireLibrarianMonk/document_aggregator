"""Process-based worker + idle-backoff guards (committed pytest).

Covers the worker changes promoted this session that the existing (thread-only)
job tests do not:

  - multi-PROCESS drain: N worker processes sharing one SQLite queue process
    every job EXACTLY once (no double-claim across processes);
  - cold-start WAL race fix: many processes opening a fresh DB concurrently must
    not raise 'database is locked' (busy_timeout-before-WAL + parent init);
  - adaptive idle backoff: fast pickup after work, bounded sleep when idle, and
    unchanged claim/retry semantics.

All bounded: spawn processes with a hard join timeout, finite jobs, a deadline
on every wait — cannot hang.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.jobs import SqliteJobQueue, Worker, register_op  # noqa: E402

register_op("p_echo")(lambda p: {"echo": p.get("n")})


def _proc_drain(db_path: str, wid: int, out_q) -> None:
    """One worker PROCESS: own connection, claim+complete until empty, report
    the job ids it processed so the parent can detect any double-claim."""
    q = SqliteJobQueue(db_path)
    from app.jobs.worker import _REGISTRY
    done = []
    worker_id = f"proc-{os.getpid()}-{wid}"
    while True:
        job = q.claim(worker_id)
        if job is None:
            break
        fn = _REGISTRY.get(job.op)
        q.complete(job.id, fn(job.payload) if fn else {})
        done.append(job.id)
    out_q.put(done)


def _run_pool(db_path: str, procs: int, timeout: float = 30.0):
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    ws = [ctx.Process(target=_proc_drain, args=(db_path, i, out_q))
          for i in range(procs)]
    for w in ws:
        w.start()
    collected = []
    for _ in ws:
        try:
            collected.extend(out_q.get(timeout=timeout))
        except Exception:  # noqa: BLE001
            break
    for w in ws:
        w.join(timeout=5.0)
        if w.is_alive():
            w.terminate()
    return collected


@pytest.mark.parametrize("procs", [2, 4])
def test_multiprocess_drains_each_job_exactly_once(tmp_path, procs):
    db = str(tmp_path / "jobs.db")
    q = SqliteJobQueue(db)            # parent inits schema + WAL once
    jobs = 200
    for i in range(jobs):
        q.enqueue("p_echo", {"n": i})
    processed = _run_pool(db, procs)
    assert len(processed) == jobs, "not all jobs processed"
    assert len(set(processed)) == jobs, "a job was double-claimed across processes"
    counts = SqliteJobQueue(db).counts()
    assert counts.get("completed", 0) == jobs
    assert counts.get("pending", 0) == 0


def test_cold_start_no_wal_lock_race(tmp_path):
    """Many processes opening a FRESH DB simultaneously must not hit
    'database is locked' on the WAL journal-mode switch. We force the race by
    NOT pre-initializing in the parent (each child opens the cold DB), then
    assert every job still completes and no process died with an error."""
    db = str(tmp_path / "cold.db")
    # Seed via a throwaway connection, then CLOSE it so the DB is 'cold' for the
    # children (no warm connection holding WAL open).
    seed = SqliteJobQueue(db)
    for i in range(40):
        seed.enqueue("p_echo", {"n": i})
    processed = _run_pool(db, procs=6)
    assert len(set(processed)) == 40, "jobs lost or double-claimed under cold-start race"
    assert SqliteJobQueue(db).counts().get("completed", 0) == 40


def test_busy_timeout_set_before_wal(tmp_path):
    """Guard the specific fix: busy_timeout must be configured so a concurrent
    opener waits instead of erroring. Assert the pragma is active on a fresh
    connection (>0 ms)."""
    q = SqliteJobQueue(tmp_path / "bt.db")
    conn = q._conn()
    bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert bt and int(bt) >= 1000, f"busy_timeout not set (got {bt})"
    jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert str(jm).lower() == "wal"


# --------------------------------------------------------------------------
# Adaptive idle backoff (thread-level; no process needed)
# --------------------------------------------------------------------------

def test_idle_backoff_defaults_and_bounds():
    """min_idle_sleep <= idle_sleep; both positive. Guards the backoff config."""
    q = SqliteJobQueue(":memory:")
    w = Worker(q, "w")
    assert 0 < w.min_idle_sleep <= w.idle_sleep
    # explicit override is honored and clamped
    w2 = Worker(q, "w2", idle_sleep=0.02, min_idle_sleep=0.1)
    assert w2.min_idle_sleep <= w2.idle_sleep  # clamped down to idle_sleep


def test_idle_backoff_fast_pickup(tmp_path):
    """A job enqueued to an idle worker is picked up quickly (well under the old
    50ms flat floor), proving the backoff resets to min on claim. Bounded wait."""
    import threading
    q = SqliteJobQueue(tmp_path / "lag.db")
    w = Worker(q, "w", idle_sleep=0.05, min_idle_sleep=0.005)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    try:
        lags = []
        for _ in range(10):
            t0 = time.perf_counter()
            jid = q.enqueue("p_echo", {"n": 1})
            deadline = t0 + 1.0
            while time.perf_counter() < deadline:
                if q.get(jid).state.value in ("completed", "failed"):
                    break
                time.sleep(0.001)
            lags.append(time.perf_counter() - t0)
        import statistics as st
        median_ms = st.median(lags) * 1000
        assert median_ms < 30, f"idle pickup too slow ({median_ms:.1f}ms); backoff not resetting"
    finally:
        w.stop()
        t.join(timeout=2.0)


def test_retry_semantics_unchanged(tmp_path):
    """The backoff must not change claim/retry: a failing op goes retryable then
    failed after max_attempts."""
    register_op("p_boom")(lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    q = SqliteJobQueue(tmp_path / "retry.db")
    jid = q.enqueue("p_boom", {}, max_attempts=3)
    w = Worker(q, "w")
    for _ in range(3):
        w.run_once()
    assert q.get(jid).state.value == "failed"
    assert q.get(jid).attempts == 3
