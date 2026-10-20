"""Worker container entrypoint.

Runs the templated worker loop against the shared SQLite job queue on the
DATA_DIR volume, dispatching to the registered real ops. Handles SIGTERM for a
clean container shutdown.

Two scaling axes (measured — see alpha_loop async regime):
  WORKER_PROCESSES  (default 1): number of worker PROCESSES. CPU-bound ops
                    (reconcile/align) scale with PROCESSES because Python
                    threads are GIL-bound (measured flat); processes scale
                    ~linearly to physical cores. Set >1 to use multiple cores.
  WORKER_CONCURRENCY (default 1): threads PER process. Threads only help
                    I/O-bound ops (GIL released on I/O). Keep at 1 for a pure
                    CPU workload; raise for I/O-heavy ops.

Examples:
    python backend/worker_main.py                      # 1 process, 1 thread
    WORKER_CONCURRENCY=4 python worker_main.py          # 1 process, 4 threads (I/O)
    WORKER_PROCESSES=4 python worker_main.py            # 4 processes (CPU)
    WORKER_PROCESSES=auto python worker_main.py         # cgroup-aware: cores-1

The claim is process-safe (SQLite BEGIN IMMEDIATE + WAL — verified no
double-claim across processes), so N processes share the one queue safely.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.jobs import Worker, registered_ops  # noqa: E402
from app.jobs import ops as _ops  # noqa: E402,F401  (registers ops on import)
from app.jobs.runtime import get_queue  # noqa: E402


def _resolve_processes() -> int:
    """WORKER_PROCESSES: an int, or 'auto' for the cgroup-aware cores-1 budget."""
    raw = os.getenv("WORKER_PROCESSES", "1").strip().lower()
    if raw in ("auto", "max"):
        try:
            from app.json_alignment.concurrency import worker_count
            return max(1, worker_count(reserve=1))
        except Exception:
            return max(1, (os.cpu_count() or 1) - 1)
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _run_worker_group(group_label: str) -> None:
    """Run `WORKER_CONCURRENCY` worker THREADS in THIS process against the shared
    queue, until SIGTERM/SIGINT. Used both for the single-process path and as the
    body of each child process in the multi-process path."""
    queue = get_queue()
    concurrency = int(os.getenv("WORKER_CONCURRENCY", "1"))
    workers = [Worker(queue, worker_id=f"worker-{os.getpid()}-{i}")
               for i in range(concurrency)]

    def _shutdown(*_a):
        for w in workers:
            w.stop()

    # Signal handlers only register in the main thread of the main interpreter
    # (Python restriction). The entrypoint and each spawned child run this in
    # their main thread; guard so the function is also safe to call from a
    # helper thread (e.g. tests) without crashing.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

    print(f"[worker] {group_label} pid={os.getpid()} threads={concurrency} "
          f"ops={registered_ops()}", flush=True)
    threads = [threading.Thread(target=w.run_forever, daemon=True) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _child_entry(idx: int) -> None:
    # Child process: re-import already done by spawn; just run a worker group.
    _run_worker_group(group_label=f"proc{idx}")


def main() -> None:
    processes = _resolve_processes()
    if processes <= 1:
        # Single-process path (unchanged default behavior).
        _run_worker_group(group_label="single")
        return

    # Multi-process path: fork N children, each a worker group. The parent
    # supervises and forwards SIGTERM/SIGINT for a clean container shutdown.
    import multiprocessing as mp

    # Initialize the queue DB ONCE in the parent before spawning children: this
    # creates the schema and switches SQLite to WAL mode under a single opener,
    # so children attach to an already-WAL database and never race on the
    # journal-mode switch (which takes an exclusive lock) at cold start.
    get_queue()

    ctx = mp.get_context("spawn")  # portable; children re-import + register ops
    children = [ctx.Process(target=_child_entry, args=(i,), daemon=False)
                for i in range(processes)]

    stopping = {"flag": False}

    def _shutdown(*_a):
        stopping["flag"] = True
        for c in children:
            if c.is_alive():
                c.terminate()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"[worker] supervisor pid={os.getpid()} processes={processes} "
          f"threads_each={os.getenv('WORKER_CONCURRENCY', '1')}", flush=True)
    for c in children:
        c.start()
    # Supervise: if a child dies unexpectedly (not during shutdown), restart it
    # so a crashing op never permanently reduces the pool.
    while not stopping["flag"]:
        for i, c in enumerate(children):
            if not c.is_alive() and not stopping["flag"]:
                print(f"[worker] proc{i} exited (code={c.exitcode}); restarting",
                      flush=True)
                nc = ctx.Process(target=_child_entry, args=(i,), daemon=False)
                nc.start()
                children[i] = nc
        time.sleep(0.5)
    for c in children:
        c.join(timeout=5.0)
        if c.is_alive():
            c.kill()


if __name__ == "__main__":
    main()
