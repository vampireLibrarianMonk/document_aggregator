"""Command-center core: sub-task + sub-agent contracts, the parallel queue, and
the deterministic order-preserving assembler.

Design:
  - A SubTask is a bounded unit of work with a STABLE id (its position in the
    plan). A SubAgent executes one SubTask and returns a TaskResult.
  - The QueueAssembler may run independent tasks CONCURRENTLY (thread pool), but
    always reassembles results sorted by the stable task order, so the final
    assembly is identical regardless of execution/finish order. This is the
    "deterministic queue assembler" requirement.
  - Determinism holds for in-process agents; model agents can vary in CONTENT
    (reported separately) but the ASSEMBLY ORDER is still deterministic.
"""
from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SubTask:
    """One bounded piece of work. `order` is the stable sort key for assembly."""
    id: str
    kind: str                       # derive_manifest | extract_draft | parse_corrections | ...
    order: int
    payload: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()   # ids that must complete first


@dataclass
class TaskResult:
    task_id: str
    kind: str
    order: int
    output: Any = None
    ok: bool = True
    error: str = ""
    agent: str = ""                 # which sub-agent produced it (for the log)
    note: str = ""


class SubAgent(Protocol):
    """Executes one SubTask. Implementations: deterministic in-process, or
    model-backed. `can_handle` lets the coordinator route by task kind."""
    name: str

    def can_handle(self, kind: str) -> bool: ...

    def run(self, task: SubTask, context: dict) -> TaskResult: ...


class QueueAssembler:
    """Runs a set of ready tasks (no outstanding deps) possibly in parallel,
    then returns results in DETERMINISTIC (task.order) order.

    `parallel=False` runs sequentially (fully reproducible, default for scoring);
    `parallel=True` uses a thread pool but still assembles deterministically."""

    def __init__(self, parallel: bool = False, max_workers: int = 4) -> None:
        self.parallel = parallel
        self.max_workers = max_workers

    def run_batch(self, tasks: list[SubTask], context: dict,
                  dispatch: Callable[[SubTask, dict], TaskResult]) -> list[TaskResult]:
        if not tasks:
            return []
        if not self.parallel or len(tasks) == 1:
            results = [dispatch(t, context) for t in tasks]
        else:
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futures = {ex.submit(dispatch, t, context): t for t in tasks}
                for fut in concurrent.futures.as_completed(futures):
                    results.append(fut.result())
        # DETERMINISTIC ASSEMBLY: sort by the stable task order, not finish order.
        results.sort(key=lambda r: (r.order, r.task_id))
        return results
