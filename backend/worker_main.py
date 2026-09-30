"""Worker container entrypoint.

Runs the templated worker loop against the shared SQLite job queue on the
DATA_DIR volume, dispatching to the registered real ops. Handles SIGTERM for a
clean container shutdown.

    python backend/worker_main.py           # single worker
    WORKER_CONCURRENCY=4 python worker_main.py   # N worker threads
"""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.jobs import Worker, registered_ops  # noqa: E402
from app.jobs import ops as _ops  # noqa: E402,F401  (registers ops on import)
from app.jobs.runtime import get_queue  # noqa: E402


def main() -> None:
    queue = get_queue()
    concurrency = int(os.getenv("WORKER_CONCURRENCY", "1"))
    workers = [Worker(queue, worker_id=f"worker-{os.getpid()}-{i}") for i in range(concurrency)]

    def _shutdown(*_a):
        for w in workers:
            w.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"[worker] pid={os.getpid()} concurrency={concurrency} ops={registered_ops()}", flush=True)
    threads = [threading.Thread(target=w.run_forever, daemon=True) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
