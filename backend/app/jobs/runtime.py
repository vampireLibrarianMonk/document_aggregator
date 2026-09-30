"""Shared job-queue runtime: one SQLite DB on the DATA_DIR volume, used by both
the API (enqueue/status) and the worker container (claim/process)."""
from __future__ import annotations

from ..config import settings
from .queue import SqliteJobQueue

_queue: SqliteJobQueue | None = None


def get_queue() -> SqliteJobQueue:
    global _queue
    if _queue is None:
        settings.ensure_dirs()
        _queue = SqliteJobQueue(settings.DATA_DIR / "jobs.db")
    return _queue
