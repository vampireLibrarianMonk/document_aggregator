"""Templated, close-to-the-metal worker.

A single generic loop claims a job, dispatches it to a registered op by name,
and records the result — no per-op process spawn, no broker, minimal overhead.
Ops are composable callables registered by name; adding a capability is just
registering another op. This keeps the worker abstract while the ops stay close
to the metal (direct function calls in-process).

Design (due diligence):
  - one loop, many ops: the loop is generic; ops carry the domain logic.
  - claim -> run -> complete/fail, with retry handled by the queue.
  - cooperative stop via a flag so it shuts down cleanly (container SIGTERM).
  - N worker threads/processes can run against the same queue safely.
"""
from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable
from typing import Any

from .progress import _CURRENT, ProgressSink
from .queue import Job, JobQueue

# op name -> callable(payload) -> result dict
WorkerOp = Callable[[dict[str, Any]], dict[str, Any]]
_REGISTRY: dict[str, WorkerOp] = {}


def register_op(name: str) -> Callable[[WorkerOp], WorkerOp]:
    """Decorator to register a worker op by name."""
    def deco(fn: WorkerOp) -> WorkerOp:
        _REGISTRY[name] = fn
        return fn
    return deco


def register(name: str, fn: WorkerOp) -> None:
    _REGISTRY[name] = fn


def registered_ops() -> list[str]:
    return sorted(_REGISTRY)


class Worker:
    def __init__(self, queue: JobQueue, worker_id: str | None = None,
                 idle_sleep: float = 0.05, *, min_idle_sleep: float = 0.005) -> None:
        self.queue = queue
        self.worker_id = worker_id or f"worker-{os.getpid()}"
        # `idle_sleep` is the MAXIMUM backoff when persistently idle (keeps CPU
        # near-zero on an empty queue). `min_idle_sleep` is the initial poll
        # interval right after work, so a job that arrives soon after another is
        # picked up quickly (low latency). The sleep grows from min->max as idle
        # cycles accumulate, then resets to min on the next claimed job. This
        # drops the single-job pickup floor from ~50ms to ~5ms under load while
        # NOT busy-spinning on a long-idle queue.
        self.idle_sleep = idle_sleep
        self.min_idle_sleep = min(min_idle_sleep, idle_sleep)
        self._stop = False
        self.processed = 0

    def stop(self) -> None:
        self._stop = True

    def run_forever(self, max_idle_cycles: int | None = None) -> None:
        idle = 0
        sleep = self.min_idle_sleep
        while not self._stop:
            job = self.queue.claim(self.worker_id)
            if job is None:
                idle += 1
                if max_idle_cycles is not None and idle >= max_idle_cycles:
                    return
                time.sleep(sleep)
                # exponential backoff toward the idle_sleep cap
                sleep = min(self.idle_sleep, sleep * 2)
                continue
            idle = 0
            sleep = self.min_idle_sleep  # reset: next job picked up fast
            self._run_job(job)

    def run_once(self) -> bool:
        """Claim and run a single job. Returns True if a job was processed."""
        job = self.queue.claim(self.worker_id)
        if job is None:
            return False
        self._run_job(job)
        return True

    def _run_job(self, job: Job) -> None:
        op = _REGISTRY.get(job.op)
        if op is None:
            self.queue.fail(job.id, f"no registered op: {job.op}")
            return
        sink = ProgressSink(self.queue, job.id)
        token = _CURRENT.set(sink)
        try:
            result = op(job.payload)
            self.queue.complete(job.id, result if isinstance(result, dict) else {"value": result})
            self.processed += 1
        except Exception as exc:  # isolate: one bad job never kills the worker
            self.queue.fail(job.id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
        finally:
            _CURRENT.reset(token)
