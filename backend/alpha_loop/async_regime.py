"""Structured async-tasking test regime (in-process, bounded, no hangs).

Measures and asserts the real properties of the job queue + worker, replacing
the misleading "200ms floor" (which was only a client poll interval):

  1. per-hop latency       enqueue / claim / complete / get — sub-ms baseline
  2. idle pickup lag       how long a job waits for an idle worker to notice it
                           (bounded by Worker.idle_sleep); the real worker floor
  3. claim safety          N concurrent workers never double-claim a job
                           (SQLite BEGIN IMMEDIATE correctness) — a hard invariant
  4. concurrency scaling   throughput vs WORKER_CONCURRENCY on CPU-light jobs
  5. retry/failure         a failing op retries up to max_attempts then fails

Design rules so this NEVER hangs:
  - every worker thread is created with a max_idle_cycles cap OR an explicit
    stop flag joined with a timeout;
  - no unbounded `while ... : pass` spin — all waits have a deadline;
  - fixed iteration counts; isolated temp DB per run.

Run:  python -m alpha_loop.async_regime           (from backend/)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.queue import JobState, SqliteJobQueue  # noqa: E402
from app.jobs.worker import Worker, register_op  # noqa: E402

# Register light test ops once (idempotent).
register_op("noop")(lambda p: {"ok": True})


@register_op("spin")
def _spin(payload):
    # A deterministic CPU-light unit of work (~microseconds) to measure
    # throughput without I/O noise.
    n = int(payload.get("n", 2000))
    s = 0
    for i in range(n):
        s += i * i
    return {"s": s}


_FAIL_SEEN: dict = {}


@register_op("flaky")
def _flaky(payload):
    # Fails the first (max_attempts-1) times, then succeeds — exercises retry.
    k = payload.get("key", "k")
    _FAIL_SEEN[k] = _FAIL_SEEN.get(k, 0) + 1
    if _FAIL_SEEN[k] < payload.get("succeed_on", 3):
        raise RuntimeError(f"flaky attempt {_FAIL_SEEN[k]}")
    return {"ok": True, "attempts": _FAIL_SEEN[k]}


def _fresh_queue() -> SqliteJobQueue:
    d = tempfile.mkdtemp(prefix="async_regime_")
    return SqliteJobQueue(os.path.join(d, "jobs.db"))


# --------------------------------------------------------------------------

def test_per_hop_latency(n: int = 50) -> dict:
    import statistics as st
    q = _fresh_queue()
    w = Worker(q, "w")

    def med_us(fn):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t0)
        return st.median(ts) * 1e6

    def full():
        q.enqueue("noop", {})
        w.run_once()

    return {
        "enqueue_us": round(med_us(lambda: q.enqueue("noop", {})), 1),
        "get_us": round(med_us(lambda: q.get(q.enqueue("noop", {}))), 1),
        "full_roundtrip_ms": round(med_us(full) / 1000, 4),
    }


def test_idle_pickup_lag(trials: int = 20, idle_sleep: float = 0.05) -> dict:
    """Start ONE idle worker in a thread, enqueue a job, measure time until it
    reaches a terminal state. Bounded: worker stopped + joined with timeout."""
    q = _fresh_queue()
    w = Worker(q, "w", idle_sleep=idle_sleep)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    lags = []
    try:
        for _ in range(trials):
            t0 = time.perf_counter()
            jid = q.enqueue("noop", {})
            # bounded wait: deadline, no spin-forever
            deadline = t0 + 2.0
            while time.perf_counter() < deadline:
                if q.get(jid).state in (JobState.completed, JobState.failed):
                    break
                time.sleep(0.001)
            lags.append(time.perf_counter() - t0)
    finally:
        w.stop()
        t.join(timeout=2.0)
    import statistics as st
    return {
        "idle_sleep_s": idle_sleep,
        "median_lag_ms": round(st.median(lags) * 1000, 2),
        "p95_lag_ms": round(sorted(lags)[int(0.95 * len(lags)) - 1] * 1000, 2),
        "note": "a job waits up to idle_sleep for an idle worker to poll",
    }


def test_claim_safety(workers: int = 8, jobs: int = 500) -> dict:
    """N worker threads drain `jobs` jobs; assert each job is processed EXACTLY
    once (no double-claim). This validates the BEGIN IMMEDIATE claim under WAL."""
    q = _fresh_queue()
    for i in range(jobs):
        q.enqueue("noop", {"i": i})
    processed: list[str] = []
    lock = threading.Lock()

    def drain(wid):
        w = Worker(q, f"w{wid}", idle_sleep=0.001)
        while True:
            job = q.claim(w.worker_id)
            if job is None:
                return
            with lock:
                processed.append(job.id)
            q.complete(job.id, {"ok": True})

    threads = [threading.Thread(target=drain, args=(i,), daemon=True)
               for i in range(workers)]
    t0 = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10.0)
    elapsed = time.perf_counter() - t0
    unique = len(set(processed))
    return {
        "workers": workers, "jobs": jobs,
        "processed_total": len(processed),
        "processed_unique": unique,
        "no_double_claim": unique == len(processed) == jobs,
        "elapsed_ms": round(elapsed * 1000, 1),
        "throughput_jobs_per_s": round(jobs / elapsed, 0),
    }


def test_concurrency_scaling(jobs: int = 400, spin_n: int = 20000,
                             worker_counts=(1, 2, 4)) -> dict:
    """Throughput vs worker count on CPU-light `spin` jobs. Reveals whether
    threads scale (GIL-bound work won't; the claim is I/O so some overlap)."""
    out = {}
    for wc in worker_counts:
        q = _fresh_queue()
        for i in range(jobs):
            q.enqueue("spin", {"n": spin_n})

        def drain(wid):
            w = Worker(q, f"w{wid}", idle_sleep=0.001)
            while True:
                job = q.claim(w.worker_id)
                if job is None:
                    return
                from app.jobs.worker import _REGISTRY
                try:
                    r = _REGISTRY[job.op](job.payload)
                    q.complete(job.id, r)
                except Exception as e:  # noqa: BLE001
                    q.fail(job.id, str(e))

        threads = [threading.Thread(target=drain, args=(i,), daemon=True)
                   for i in range(wc)]
        t0 = time.perf_counter()
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30.0)
        elapsed = time.perf_counter() - t0
        out[wc] = round(jobs / elapsed, 0)
    base = out[worker_counts[0]]
    return {"throughput_by_workers": out,
            "scaling_vs_1": {wc: round(v / base, 2) for wc, v in out.items()}}


def test_retry(max_attempts: int = 3) -> dict:
    q = _fresh_queue()
    w = Worker(q, "w")
    _FAIL_SEEN.clear()
    jid = q.enqueue("flaky", {"key": "r1", "succeed_on": max_attempts},
                    max_attempts=max_attempts)
    # run_once up to max_attempts+1 times (bounded); job goes retryable->...->completed
    final = None
    for _ in range(max_attempts + 2):
        if not w.run_once():
            break
        st = q.get(jid).state
        final = st.value
        if st in (JobState.completed, JobState.failed):
            break
    return {"max_attempts": max_attempts, "final_state": final,
            "attempts_taken": _FAIL_SEEN.get("r1"),
            "recovered": final == "completed"}


def main() -> int:
    print("=== Structured async-tasking regime (in-process, bounded) ===\n")
    print("1. per-hop latency:   ", test_per_hop_latency())
    print("2. idle pickup lag:   ", test_idle_pickup_lag())
    safety = test_claim_safety()
    print("3. claim safety:      ", safety)
    print("4. concurrency scaling:", test_concurrency_scaling())
    print("5. retry/failure:     ", test_retry())
    ok = safety["no_double_claim"]
    print(f"\nHARD INVARIANT (no double-claim): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
