"""The adversarial red team: three agents that check the loop's work.

A  CorrectnessAdversary   — per decision, judges the engine's mapping against
                            the generator's threshold-independent ground truth
                            (recovered / missed / false_match), and enforces the
                            sacred no-fabrication line (no value was invented).
B  InvariantAdversary      — cross-round properties: approving one shape must
                            NOT cascade to others; the SAME shape re-fired must
                            replay with zero re-prompt; a type-flip must NOT ride
                            the clean replay path.
C  PerformanceAdversary    — file-processing throughput, queue wait, worker
                            handling, and a per-file budget. Proposes efficiency
                            changes; flags a regression past the budget.

Each agent returns Verdict objects. A FAIL verdict is a "break" the controller
captures and we learn from. The agents are intentionally independent of the
Decider so they genuinely check its work rather than rubber-stamp it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import JobResult
from .generator import FiredDoc


@dataclass
class Verdict:
    agent: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return "PASS" if self.ok else "FAIL"


def _bare(path: str) -> str:
    """Leaf key of an engine source path ('payload.title[]' -> 'title')."""
    return path.split(".")[-1].replace("[]", "")


# --------------------------------------------------------------------------
# A — correctness: mapping truth + no fabrication
# --------------------------------------------------------------------------

class CorrectnessAdversary:
    """Scores one shape's learned mapping against its carried ground truth.

    Reads the library entry for the shape's provisional profile: `mapped`
    (golden -> source_path), `needs_review`, `conflicts`. Scores exactly like
    the alpha_frontier harness: a confident map to a NON-true key is a
    false_match (precision loss); a present golden with no confident map is a
    miss (recall loss). A FAIL is any false_match on a REQUIRED field, or any
    sign a value was fabricated.
    """

    name = "A:correctness"

    def check(self, doc: FiredDoc, entry: dict[str, Any],
              conformed_records: list[dict[str, Any]] | None) -> Verdict:
        mapped: dict[str, str] = entry.get("mapped", {})
        truth = doc.truth
        present = doc.present_golden

        # golden -> set of true source leaf keys
        truth_inv: dict[str, set[str]] = {}
        for k, g in truth.items():
            if g is not None:
                truth_inv.setdefault(g, set()).add(_bare(k))

        recovered = missed = false_match = 0
        req_false: list[str] = []
        detail_rows: list[dict[str, Any]] = []
        for g in present:
            src = mapped.get(g)
            if src is None:
                missed += 1
                detail_rows.append({"golden": g, "verdict": "missed"})
                continue
            if _bare(src) in truth_inv.get(g, set()):
                recovered += 1
            else:
                false_match += 1
                detail_rows.append({"golden": g, "mapped_to": src,
                                    "verdict": "false_match"})
                if g in self._required(doc):
                    req_false.append(g)
        # absent goldens that were confidently mapped = fabricated correspondence
        for g, src in mapped.items():
            if g not in present:
                false_match += 1
                req_false.append(g) if g in self._required(doc) else None
                detail_rows.append({"golden": g, "mapped_to": src,
                                    "verdict": "false_match(absent)"})

        # no-fabrication on VALUES: every conformed value must trace to a source
        # value present in the doc's records (never computed/invented).
        fabricated_values = self._fabricated_values(doc, conformed_records)

        ok = not req_false and not fabricated_values
        summary = (f"recovered={recovered} missed={missed} "
                   f"false_match={false_match}")
        if req_false:
            summary += f" · REQUIRED false_match on {sorted(set(req_false))}"
        if fabricated_values:
            summary += f" · FABRICATED values {fabricated_values[:3]}"
        return Verdict(self.name, ok, summary, {
            "recovered": recovered, "missed": missed, "false_match": false_match,
            "required_false_match": sorted(set(req_false)),
            "fabricated_values": fabricated_values,
            "rows": detail_rows,
        })

    @staticmethod
    def _required(doc: FiredDoc) -> set[str]:
        from .generator import REQUIRED_FIELDS
        return set(REQUIRED_FIELDS)

    @staticmethod
    def _fabricated_values(doc: FiredDoc,
                           conformed: list[dict[str, Any]] | None) -> list[Any]:
        """A conformed scalar value that appears in NO source record is a
        fabrication. Compares against the flat multiset of source values. This
        is a conservative check (string/number equality); it never flags a value
        that was legitimately copied from the source."""
        if not conformed:
            return []
        source_values: set[Any] = set()
        for rec in doc.records:
            _collect_scalars(rec, source_values)
        fabricated: list[Any] = []
        for rec in conformed:
            got: set[Any] = set()
            _collect_scalars(rec, got)
            for v in got:
                if v not in source_values and v not in (None, "", []):
                    fabricated.append(v)
        return fabricated


def _collect_scalars(obj: Any, out: set[Any]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_scalars(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_scalars(v, out)
    elif isinstance(obj, (str, int, float, bool)):
        # normalize numbers-as-strings so a benign cast isn't a false alarm
        out.add(str(obj) if not isinstance(obj, bool) else obj)


# --------------------------------------------------------------------------
# B — invariants: cascade + replay + drift banding
# --------------------------------------------------------------------------

class InvariantAdversary:
    """Checks cross-round structural invariants from library snapshots."""

    name = "B:invariants"

    def check_no_cascade(self, before: dict[str, Any], after: dict[str, Any],
                         approved_id: str) -> Verdict:
        """Approving `approved_id` must flip ONLY that entry to approved; every
        other entry's state must be unchanged."""
        bstate = {e["profile_id"]: e["state"] for e in before.get("entries", [])}
        astate = {e["profile_id"]: e["state"] for e in after.get("entries", [])}
        changed = [pid for pid in astate
                   if bstate.get(pid) != astate.get(pid)]
        unexpected = [pid for pid in changed if pid != approved_id]
        ok = unexpected == [] and astate.get(approved_id) == "approved"
        if ok:
            summary = f"only {approved_id} changed -> approved (no cascade)"
        else:
            summary = (f"CASCADE/approval anomaly: changed={changed}, "
                       f"unexpected={unexpected}")
        return Verdict(self.name, ok, summary, {
            "changed": changed, "unexpected": unexpected,
            "approved_state": astate.get(approved_id)})

    def check_replay(self, refire_outcomes: list[dict[str, Any]],
                     shape_label: str) -> Verdict:
        """A re-fired, already-approved shape must replay: its cluster takes the
        replay_clean pathway and produces NO new provisional profile (zero
        re-prompt)."""
        pathways = [o.get("pathway") for o in refire_outcomes]
        new_provisional = [o.get("provisional_profile") for o in refire_outcomes
                           if o.get("provisional_profile")]
        replayed = any(p == "replay_clean" for p in pathways)
        ok = replayed and not new_provisional
        if ok:
            summary = f"{shape_label} replayed clean, zero re-prompt"
        else:
            summary = (f"{shape_label} did NOT replay: pathways={pathways}, "
                       f"new_provisional={new_provisional}")
        return Verdict(self.name, ok, summary, {
            "pathways": pathways, "new_provisional": new_provisional})


# --------------------------------------------------------------------------
# C — performance: throughput, queue, worker handling
# --------------------------------------------------------------------------

@dataclass
class PerfSample:
    files: int
    records: int
    queue_wait_s: float | None
    total_s: float | None
    worker_id: str | None

    @property
    def per_file_s(self) -> float | None:
        if self.total_s is None or self.files == 0:
            return None
        return self.total_s / self.files


class PerformanceAdversary:
    """Measures the file-processing path and flags a budget regression.

    Budget: a soft per-file wall-clock ceiling. A sample over budget is a FAIL
    the controller surfaces (a perf 'break'); the detail carries a concrete
    tuning suggestion so we can act, not just observe."""

    name = "C:performance"

    def __init__(self, per_file_budget_s: float = 2.5) -> None:
        self.per_file_budget_s = per_file_budget_s
        self.history: list[PerfSample] = []

    def measure(self, job: JobResult, files: int, records: int,
                queue_counts: dict[str, int]) -> Verdict:
        sample = PerfSample(files=files, records=records,
                            queue_wait_s=job.queue_wait_s, total_s=job.total_s,
                            worker_id=job.worker_id)
        self.history.append(sample)
        pf = sample.per_file_s
        over = pf is not None and pf > self.per_file_budget_s
        suggestion = self._suggest(sample, queue_counts)
        ok = not over
        qw = f"{sample.queue_wait_s:.2f}s" if sample.queue_wait_s is not None else "?"
        tt = f"{sample.total_s:.2f}s" if sample.total_s is not None else "?"
        pfs = f"{pf:.2f}s" if pf is not None else "?"
        summary = (f"{files} file(s)/{records} rec · queue_wait={qw} "
                   f"total={tt} per_file={pfs} worker={sample.worker_id}")
        if over:
            summary += f" · OVER budget {self.per_file_budget_s}s/file"
        return Verdict(self.name, ok, summary, {
            "per_file_s": pf, "queue_wait_s": sample.queue_wait_s,
            "total_s": sample.total_s, "queue_counts": queue_counts,
            "suggestion": suggestion})

    def _suggest(self, s: PerfSample, counts: dict[str, int]) -> str:
        pending = counts.get("pending", 0)
        if s.queue_wait_s is not None and s.queue_wait_s > 1.0:
            return ("queue wait high: start more worker threads "
                    "(WORKER_CONCURRENCY) or add a second worker process")
        if pending > 1:
            return (f"{pending} jobs pending behind this one: the worker is "
                    "saturated; raise WORKER_CONCURRENCY")
        if s.per_file_s is not None and s.per_file_s > self.per_file_budget_s:
            return ("per-file time high with an idle queue: the cost is inside "
                    "the batch op (clustering/inference) — profile the cluster "
                    "pathway; consider chunking very large files")
        return "within budget; headroom for larger batches"
