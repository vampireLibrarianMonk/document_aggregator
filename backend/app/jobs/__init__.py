"""Job queue + async worker.

A minimal, close-to-the-metal job system: a queue behind an interface (SQLite
now, Postgres-ready) and a templated worker that dispatches composable ops. No
microservices, no broker — the reference architecture says start boring and use
the database. SQLite gives real transactional claim safety locally and the same
`claim` semantics map onto Postgres `FOR UPDATE SKIP LOCKED` later.
"""
from .queue import Job, JobQueue, JobState, SqliteJobQueue
from .worker import Worker, WorkerOp, register_op, registered_ops

__all__ = ["Job", "JobState", "JobQueue", "SqliteJobQueue",
           "Worker", "WorkerOp", "register_op", "registered_ops"]
