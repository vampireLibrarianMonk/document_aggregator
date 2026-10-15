"""Progress channel for worker ops.

An op may publish per-unit progress without changing its single-arg calling
convention. The worker installs a `ProgressSink` into a `ContextVar` around the
op call; the op calls `publish_progress(done, total)`, which routes through the
sink to `JobQueue.set_progress`. Progress is observational only — a failed tick
is swallowed so it can never fail the batch (NFR1.3, design §2.9). When no sink
is in context (every op other than batch_align, or a direct non-worker call),
`publish_progress` is a no-op.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

from .queue import JobQueue

log = logging.getLogger(__name__)


class ProgressSink:
    """Marshals progress ticks from the op to the job queue for one job."""

    def __init__(self, queue: JobQueue, job_id: str) -> None:
        self.queue = queue
        self.job_id = job_id
        # True once any tick has advanced past 0; drives the NIT-11 completion
        # warning (a batch with files that never ticked is a bug worth flagging).
        self.advanced = False

    def set(self, done: int, total: int) -> None:
        self.queue.set_progress(self.job_id, done, total)
        if done > 0:
            self.advanced = True


_CURRENT: ContextVar[ProgressSink | None] = ContextVar("progress_sink", default=None)


def current_sink() -> ProgressSink | None:
    """The sink installed for the running op, or None outside a worker op."""
    return _CURRENT.get()


def publish_progress(done: int, total: int) -> None:
    """Publish a progress tick through the current sink. No-op when no sink is in
    context. Never raises — a failed tick is logged at debug and swallowed so a
    progress hiccup can never fail the batch (NFR1.3)."""
    sink = _CURRENT.get()
    if sink is None:
        return
    try:
        sink.set(done, total)
    except Exception:  # noqa: BLE001 - progress is observational only
        log.debug("progress tick failed (done=%s total=%s)", done, total, exc_info=True)
