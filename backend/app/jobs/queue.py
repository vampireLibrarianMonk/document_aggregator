"""Job queue: interface + SQLite implementation.

The `JobQueue` protocol is the stable seam. `SqliteJobQueue` gives transactional,
single-writer-safe claim semantics locally (WAL mode, an atomic UPDATE ... WHERE
state='pending' claim) that map directly onto PostgreSQL's
`SELECT ... FOR UPDATE SKIP LOCKED` when we upgrade. States and columns mirror
the reference spec's job model (§36): attempts, timestamps, worker id, error.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class JobState(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    retryable = "retryable"


@dataclass
class Job:
    id: str
    op: str
    payload: dict[str, Any]
    state: JobState = JobState.pending
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 100
    result: dict[str, Any] | None = None
    error: str | None = None
    worker_id: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None


class JobQueue(Protocol):
    def enqueue(self, op: str, payload: dict, priority: int = 100, max_attempts: int = 3) -> str: ...
    def claim(self, worker_id: str) -> Job | None: ...
    def complete(self, job_id: str, result: dict) -> None: ...
    def fail(self, job_id: str, error: str) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def counts(self) -> dict[str, int]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    op TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    priority INTEGER NOT NULL DEFAULT 100,
    result TEXT,
    error TEXT,
    worker_id TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claimable ON jobs(state, priority, created_at);
"""


class SqliteJobQueue:
    """Transactional job queue. Safe for many workers against one DB file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        c = self._conn()
        c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        """One connection per thread, reused (SQLite connections are not safe to
        share across threads, but reusing per-thread avoids per-op open cost)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def enqueue(self, op: str, payload: dict, priority: int = 100, max_attempts: int = 3) -> str:
        job_id = "job_" + uuid.uuid4().hex[:12]
        self._conn().execute(
            "INSERT INTO jobs (id, op, payload, priority, max_attempts, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, op, json.dumps(payload), priority, max_attempts, time.time()),
        )
        return job_id

    def claim(self, worker_id: str) -> Job | None:
        """Atomically claim the highest-priority claimable job. Uses an
        IMMEDIATE transaction so concurrent workers never double-claim."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE;")
        try:
            row = c.execute(
                "SELECT * FROM jobs WHERE state IN ('pending','retryable') "
                "ORDER BY priority, created_at LIMIT 1"
            ).fetchone()
            if row is None:
                c.execute("COMMIT;")
                return None
            c.execute(
                "UPDATE jobs SET state='processing', attempts=attempts+1, "
                "worker_id=?, started_at=? WHERE id=?",
                (worker_id, time.time(), row["id"]),
            )
            c.execute("COMMIT;")
        except Exception:
            c.execute("ROLLBACK;")
            raise
        job = self._row_to_job(row)
        job.state = JobState.processing
        job.attempts += 1
        job.worker_id = worker_id
        return job

    def complete(self, job_id: str, result: dict) -> None:
        self._conn().execute(
            "UPDATE jobs SET state='completed', result=?, finished_at=? WHERE id=?",
            (json.dumps(result), time.time(), job_id),
        )

    def fail(self, job_id: str, error: str) -> None:
        """Mark failed, or retryable if attempts remain."""
        c = self._conn()
        row = c.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return
        state = "retryable" if row["attempts"] < row["max_attempts"] else "failed"
        c.execute("UPDATE jobs SET state=?, error=?, finished_at=? WHERE id=?",
                  (state, error, time.time(), job_id))

    def get(self, job_id: str) -> Job | None:
        row = self._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def counts(self) -> dict[str, int]:
        rows = self._conn().execute("SELECT state, COUNT(*) n FROM jobs GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"], op=row["op"], payload=json.loads(row["payload"]),
            state=JobState(row["state"]), attempts=row["attempts"],
            max_attempts=row["max_attempts"], priority=row["priority"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"], worker_id=row["worker_id"],
            created_at=row["created_at"], started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
