"""Process-based worker prototype — proving CPU scaling past the GIL.

The async regime showed thread concurrency is FLAT for CPU-bound ops (GIL). This
prototype runs N worker PROCESSES against the shared SQLite queue and measures
whether CPU-bound work now scales ~linearly with cores. It also re-validates the
claim invariant ACROSS PROCESSES (BEGIN IMMEDIATE + WAL must prevent
double-claim between processes, not just threads).

Everything is bounded: each process drains until the queue is empty then exits;
the parent joins with a timeout; a shared counter records processed job ids so
double-claims are detectable.

Reuses the existing cgroup-aware core budget (available_cores / worker_count) so
a process pool respects a container's CPU limit exactly like the batch pool.

Run:  python -m alpha_loop.proc_worker            (from backend/)
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.queue import SqliteJobQueue  # noqa: E402
from app.jobs.worker import _REGISTRY, register_op  # noqa: E402

# CPU-bound unit of work (pure Python, GIL-bound in a thread). Registered in the
# child via import of this module (ops register at import time).
register_op("cpu")(lambda p: {"s": sum(i * i for i in range(int(p.get("n", 50000))))})


def _drain_process(db_path: str, wid: int, out_q) -> None:
    """One worker PROCESS: own SQLite connection, claim+run until empty, report
    processed job ids via a multiprocessing queue so the parent can check for
    double-claims across processes."""
    q = SqliteJobQueue(db_path)
    worker_id = f"proc-{os.getpid()}-{wid}"
    processed = []
    while True:
        job = q.claim(worker_id)
        if job is None:
            break
        fn = _REGISTRY.get(job.op)
        try:
            q.complete(job.id, fn(job.payload) if fn else {})
        except Exception as e:  # noqa: BLE001
            q.fail(job.id, str(e))
        processed.append(job.id)
    out_q.put(processed)


def run_process_pool(db_path: str, procs: int, *, join_timeout: float = 60.0):
    """Fork `procs` worker processes draining the queue; return processed ids."""
    ctx = mp.get_context("spawn")  # spawn is safe + portable (no fork-in-thread)
    out_q = ctx.Queue()
    workers = [ctx.Process(target=_drain_process, args=(db_path, i, out_q))
               for i in range(procs)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    # Collect results FIRST (processes put before exiting), then join bounded.
    collected = []
    for _ in workers:
        try:
            collected.extend(out_q.get(timeout=join_timeout))
        except Exception:  # noqa: BLE001
            break
    for w in workers:
        w.join(timeout=5.0)
        if w.is_alive():
            w.terminate()
    return collected, time.perf_counter() - t0


def _seed(db_path: str, jobs: int, n: int) -> None:
    q = SqliteJobQueue(db_path)
    for i in range(jobs):
        q.enqueue("cpu", {"n": n, "i": i})


def bench(jobs: int = 400, n: int = 200000, proc_counts=(1, 2, 4, 8)) -> dict:
    """Throughput vs process count on CPU-bound jobs. Each count gets a fresh DB.
    Also asserts no double-claim across processes."""
    out = {}
    safety_ok = True
    for pc in proc_counts:
        d = tempfile.mkdtemp(prefix="proc_worker_")
        db = os.path.join(d, "jobs.db")
        _seed(db, jobs, n)
        processed, elapsed = run_process_pool(db, pc)
        unique = len(set(processed))
        if not (unique == len(processed) == jobs):
            safety_ok = False
        out[pc] = round(jobs / elapsed, 0) if elapsed > 0 else 0
    base = out[proc_counts[0]]
    return {
        "throughput_by_procs": out,
        "scaling_vs_1": {pc: round(v / base, 2) for pc, v in out.items()},
        "no_double_claim_across_processes": safety_ok,
    }


def main() -> int:
    from app.json_alignment.concurrency import available_cores, worker_count
    print("=== Process-worker CPU scaling (vs the GIL-bound thread result) ===\n")
    print(f"cores available: {available_cores()}  recommended workers: {worker_count()}\n")
    res = bench()
    print("throughput (jobs/s) by PROCESS count:")
    for pc, tp in res["throughput_by_procs"].items():
        print(f"  {pc} proc: {tp:>8.0f} jobs/s   ({res['scaling_vs_1'][pc]}x vs 1)")
    print(f"\nno double-claim across processes: "
          f"{'PASS' if res['no_double_claim_across_processes'] else 'FAIL'}")
    print("\nContrast: thread concurrency was FLAT (1.0/1.02/0.92x) — GIL-bound.")
    return 0 if res["no_double_claim_across_processes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
