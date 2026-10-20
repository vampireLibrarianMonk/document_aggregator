"""Thin live-API client for the Adversarial Alpha Loop.

Everything here talks to the RUNNING stack over HTTP (not in-process): that is
the whole point of the apparatus — we exercise the real batch endpoint, the
real async job queue, the real worker, and the real library persistence across
rounds. No app internals are imported; only the public REST surface is used, so
the loop measures what a real client experiences.

Uses only the Python standard library (urllib) so the apparatus has no extra
dependency and runs anywhere the stack does.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ApiError(RuntimeError):
    """A non-2xx response from the stack, carrying status + parsed detail."""

    def __init__(self, status: int, detail: str, path: str) -> None:
        super().__init__(f"{status} {path}: {detail}")
        self.status = status
        self.detail = detail
        self.path = path


@dataclass
class JobResult:
    """A terminal (or last-observed) job state plus the timing the perf adversary
    reads: queue wait (enqueue->first processing) and run time."""
    job_id: str
    state: str
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    worker_id: str | None
    enqueued_at: float
    first_seen_processing: float | None
    finished_at: float | None

    @property
    def queue_wait_s(self) -> float | None:
        if self.first_seen_processing is None:
            return None
        return self.first_seen_processing - self.enqueued_at

    @property
    def total_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return self.finished_at - self.enqueued_at


class AlphaClient:
    """Minimal REST client for the endpoints the loop drives."""

    def __init__(self, base: str = "http://localhost:8001", timeout: float = 30.0) -> None:
        # Default 8001 — the local dev API port the loop fires against (8000 is
        # reserved for the user's other app in this environment).
        self.base = base.rstrip("/")
        self.timeout = timeout

    # -- low-level -----------------------------------------------------------

    def _req(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (json.JSONDecodeError, AttributeError):
                # Error body wasn't JSON (or not an object) — keep the raw text.
                # Not silent: the ApiError below always propagates the detail.
                pass
            raise ApiError(e.code, str(detail), path) from None

    # -- health / projects ---------------------------------------------------

    def health(self) -> bool:
        try:
            return self._req("GET", "/health").get("status") == "ok"
        except Exception:
            return False

    def create_project(self, name: str) -> str:
        """Create a json_alignment project and return its id."""
        p = self._req("POST", "/projects",
                      {"name": name, "kind": "json_alignment"})
        return p["id"]

    def set_target_schema(self, project_id: str, schema: dict) -> dict:
        return self._req("PUT", f"/projects/{project_id}/alignment/target-schema",
                         {"target": schema})

    def set_reject_below(self, project_id: str, value: float) -> dict:
        return self._req("PATCH", f"/projects/{project_id}",
                         {"reject_below": value})

    # -- batch ---------------------------------------------------------------

    def submit_batch(self, project_id: str, docs: list[dict],
                     research: bool = True) -> str:
        """Queue a batch; return the job_id to poll."""
        r = self._req("POST", f"/projects/{project_id}/alignment/batch",
                      {"docs": docs, "research": research})
        return r["job_id"]

    def job(self, job_id: str) -> dict:
        return self._req("GET", f"/jobs/{job_id}")

    def job_counts(self) -> dict[str, int]:
        return self._req("GET", "/jobs").get("counts", {})

    def wait_for_job(self, job_id: str, *, poll_s: float = 0.2,
                     timeout_s: float = 120.0) -> JobResult:
        """Poll a job to a terminal state, capturing the timings the perf
        adversary needs (first-processing + finish). Raises on timeout."""
        enqueued = time.time()
        first_processing: float | None = None
        deadline = enqueued + timeout_s
        while True:
            j = self.job(job_id)
            state = j.get("state")
            if state == "processing" and first_processing is None:
                first_processing = time.time()
            if state in ("completed", "failed"):
                return JobResult(
                    job_id=job_id, state=state, result=j.get("result"),
                    error=j.get("error"), attempts=j.get("attempts", 0),
                    worker_id=j.get("worker_id"), enqueued_at=enqueued,
                    first_seen_processing=first_processing,
                    finished_at=time.time())
            if time.time() > deadline:
                raise TimeoutError(
                    f"job {job_id} did not finish within {timeout_s}s "
                    f"(last state={state}); is the worker running?")
            time.sleep(poll_s)

    # -- library / decisions -------------------------------------------------

    def library(self, project_id: str) -> dict:
        return self._req("GET", f"/projects/{project_id}/alignment/library")

    def approve(self, project_id: str, profile_id: str) -> dict:
        return self._req("POST", f"/projects/{project_id}/alignment/approve",
                         {"profile_id": profile_id})

    def reject(self, project_id: str, profile_id: str, category: str,
               fields: list[str] | None = None, note: str | None = None) -> dict:
        body: dict[str, Any] = {"profile_id": profile_id, "category": category}
        if fields:
            body["fields"] = fields
        if note:
            body["note"] = note
        return self._req("POST", f"/projects/{project_id}/alignment/reject", body)

    def override(self, project_id: str, profile_id: str, target: str,
                 source_path: str | None) -> dict:
        return self._req("POST", f"/projects/{project_id}/alignment/override",
                         {"profile_id": profile_id, "target": target,
                          "source_path": source_path})

    def outputs(self, project_id: str) -> list[dict]:
        return self._req("GET",
                         f"/projects/{project_id}/alignment/outputs").get("outputs", [])
