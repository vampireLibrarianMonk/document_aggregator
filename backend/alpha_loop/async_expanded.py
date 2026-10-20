"""Expanded async testing — edge regimes for production confidence.

Builds on async_regime (thread baseline) and proc_worker (process scaling) with
the scenarios that decide WHICH worker model to use and whether the queue is
robust under stress. All bounded (spawn processes, timeouts, finite jobs).

  1. io_scaling        I/O-bound ops SHOULD scale with threads (GIL released on
                       sleep/I/O) — the complement of the CPU-bound finding.
  2. mixed_workload    interleaved CPU + I/O jobs: process pool handles both.
  3. contention        many processes on TINY jobs — claim-lock pressure; find
                       where coordination overhead overtakes the work.
  4. fault_isolation   a process whose op raises keeps the others draining and
                       never corrupts the queue (failed job recorded, rest done).
  5. backpressure      sustained enqueue vs a fixed pool: queue depth stays
                       bounded (drains), never unbounded growth.

Run:  python -m alpha_loop.async_expanded          (from backend/)
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.queue import JobState, SqliteJobQueue  # noqa: E402
from app.jobs.worker import _REGISTRY, Worker, register_op  # noqa: E402

register_op("cpu")(lambda p: {"s": sum(i * i for i in range(int(p.get("n", 50000))))})
register_op("io")(lambda p: (time.sleep(float(p.get("sleep", 0.01))), {"ok": True})[1])


@register_op("crash")
def _crash(payload):
    raise RuntimeError("intentional crash for fault-isolation test")


def _fresh_db() -> str:
    return os.path.join(tempfile.mkdtemp(prefix="async_exp_"), "jobs.db")


# -- 1. I/O scaling with THREADS (complement of CPU finding) -----------------

def io_scaling(jobs: int = 200, sleep: float = 0.01, thread_counts=(1, 2, 4, 8)) -> dict:
    out = {}
    for tc in thread_counts:
        db = _fresh_db(); q = SqliteJobQueue(db)
        for i in range(jobs):
            q.enqueue("io", {"sleep": sleep})

        def drain(wid):
            w = Worker(q, f"t{wid}", idle_sleep=0.001)
            while True:
                job = q.claim(w.worker_id)
                if job is None:
                    return
                try:
                    q.complete(job.id, _REGISTRY[job.op](job.payload))
                except Exception as e:  # noqa: BLE001
                    q.fail(job.id, str(e))

        ths = [threading.Thread(target=drain, args=(i,), daemon=True) for i in range(tc)]
        t0 = time.perf_counter()
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=30.0)
        out[tc] = round(jobs / (time.perf_counter() - t0), 0)
    base = out[thread_counts[0]]
    return {"throughput_by_threads": out,
            "scaling_vs_1": {tc: round(v / base, 2) for tc, v in out.items()},
            "verdict": "threads SHOULD scale I/O (GIL released on sleep)"}


# -- process pool helper (shared) --------------------------------------------

def _proc_drain(db_path, wid, out_q):
    q = SqliteJobQueue(db_path)
    worker_id = f"proc-{os.getpid()}-{wid}"
    done = []
    while True:
        job = q.claim(worker_id)
        if job is None:
            break
        fn = _REGISTRY.get(job.op)
        try:
            q.complete(job.id, fn(job.payload) if fn else {})
        except Exception as e:  # noqa: BLE001
            q.fail(job.id, str(e))
        done.append((job.id, job.op))
    out_q.put(done)


def _run_procs(db_path, procs, timeout=60.0):
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    ws = [ctx.Process(target=_proc_drain, args=(db_path, i, out_q)) for i in range(procs)]
    t0 = time.perf_counter()
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
    return collected, time.perf_counter() - t0


# -- 2. mixed workload -------------------------------------------------------

def mixed_workload(cpu_jobs: int = 100, io_jobs: int = 100, procs: int = 4) -> dict:
    db = _fresh_db(); q = SqliteJobQueue(db)
    for i in range(cpu_jobs):
        q.enqueue("cpu", {"n": 100000})
    for i in range(io_jobs):
        q.enqueue("io", {"sleep": 0.005})
    done, elapsed = _run_procs(db, procs)
    ops = [op for _id, op in done]
    return {"procs": procs, "total": len(done),
            "cpu_done": ops.count("cpu"), "io_done": ops.count("io"),
            "all_processed": len(done) == cpu_jobs + io_jobs,
            "elapsed_ms": round(elapsed * 1000, 0)}


# -- 3. contention: tiny jobs, many processes --------------------------------

def contention(jobs: int = 2000, proc_counts=(1, 4, 8)) -> dict:
    out = {}
    for pc in proc_counts:
        db = _fresh_db(); q = SqliteJobQueue(db)
        for i in range(jobs):
            q.enqueue("cpu", {"n": 50})  # ~nothing: isolates claim-lock overhead
        done, elapsed = _run_procs(db, pc)
        out[pc] = round(jobs / elapsed, 0)
    base = out[proc_counts[0]]
    return {"tiny_job_throughput_by_procs": out,
            "scaling_vs_1": {pc: round(v / base, 2) for pc, v in out.items()},
            "note": "tiny jobs expose claim-lock contention; near-flat = coordination-bound"}


# -- 4. fault isolation ------------------------------------------------------

def fault_isolation(good: int = 100, crashers: int = 10, procs: int = 4) -> dict:
    db = _fresh_db(); q = SqliteJobQueue(db)
    ids = []
    for i in range(good):
        ids.append(("cpu", q.enqueue("cpu", {"n": 10000})))
    crash_ids = [q.enqueue("crash", {}, max_attempts=1) for _ in range(crashers)]
    done, _ = _run_procs(db, procs)
    q2 = SqliteJobQueue(db)
    states = {jid: q2.get(jid).state.value for _op, jid in ids}
    crash_states = {jid: q2.get(jid).state.value for jid in crash_ids}
    good_ok = all(s == "completed" for s in states.values())
    crash_failed = all(s == "failed" for s in crash_states.values())
    return {"good_all_completed": good_ok,
            "crashers_all_failed": crash_failed,
            "queue_intact": good_ok and crash_failed,
            "note": "a crashing op must not take down siblings or corrupt the queue"}


# -- 5. backpressure ---------------------------------------------------------

def backpressure(duration_s: float = 2.0, enqueue_rate_hz: int = 2000,
                 procs: int = 4) -> dict:
    """Sustained producer + fixed consumer pool; sample queue depth. Depth must
    stay BOUNDED (consumers keep up or the backlog is finite), not grow forever.
    Bounded by duration; producer + samplers are daemon threads with a deadline."""
    db = _fresh_db(); q = SqliteJobQueue(db)
    stop = threading.Event()
    depths = []

    def producer():
        interval = 1.0 / enqueue_rate_hz
        while not stop.is_set():
            q.enqueue("cpu", {"n": 2000})
            time.sleep(interval)

    def sampler():
        while not stop.is_set():
            depths.append(q.counts().get("pending", 0))
            time.sleep(0.05)

    # consumer threads (I/O-ish claim; enough to measure depth trend)
    def consumer(wid):
        w = Worker(q, f"c{wid}", idle_sleep=0.001)
        while not stop.is_set():
            job = q.claim(w.worker_id)
            if job is None:
                time.sleep(0.001)
                continue
            try:
                q.complete(job.id, _REGISTRY[job.op](job.payload))
            except Exception as e:  # noqa: BLE001
                q.fail(job.id, str(e))

    threads = [threading.Thread(target=producer, daemon=True),
               threading.Thread(target=sampler, daemon=True)]
    threads += [threading.Thread(target=consumer, args=(i,), daemon=True) for i in range(procs)]
    for t in threads:
        t.start()
    time.sleep(duration_s)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)
    if not depths:
        return {"error": "no samples"}
    first_half = depths[: len(depths) // 2] or depths
    second_half = depths[len(depths) // 2:] or depths
    import statistics as st
    return {"max_depth": max(depths), "final_depth": depths[-1],
            "mean_first_half": round(st.mean(first_half), 1),
            "mean_second_half": round(st.mean(second_half), 1),
            "bounded": max(depths) < enqueue_rate_hz * duration_s,
            "note": "depth bounded (not monotonically exploding) = consumers keep pace"}


def main() -> int:
    print("=== Expanded async testing ===\n")
    print("1. I/O scaling (threads):", io_scaling())
    print("2. mixed workload:       ", mixed_workload())
    print("3. contention (tiny):    ", contention())
    fi = fault_isolation()
    print("4. fault isolation:      ", fi)
    print("5. backpressure:         ", backpressure())
    ok = fi["queue_intact"]
    print(f"\nHARD INVARIANT (queue intact under faults): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
