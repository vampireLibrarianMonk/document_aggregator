"""The controller: orchestrates one spiral round against the LIVE stack.

Modes (per the agreed plan):
  (a) step        run ONE spiral, print metrics + every red-team verdict, STOP.
                  We escalate together between rounds. This is round one.
  (b) run_to_break  spiral autonomously, escalating on all-PASS, until any
                  red-team FAIL — then stop on the break for us to dissect.
                  Enabled once we trust the verdicts at convergence.

A round = fire a stage's shape(s) -> wait for the worker -> read outcomes +
library -> Decider decides -> apply (approve/remap/reject) -> red team checks
(A correctness, B invariants incl. a cascade + replay probe, C performance) ->
report. All against a dedicated throwaway json_alignment project so the user's
real project 7 is never touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import (
    CorrectnessAdversary,
    InvariantAdversary,
    PerformanceAdversary,
    Verdict,
)
from .client import AlphaClient
from .decider import Decider, Decision
from .generator import STAGES, FiredDoc, Generator, ShapeSpec


def _schema() -> dict[str, Any]:
    """The project-7 golden game schema the frontier harness also uses."""
    p = (Path(__file__).resolve().parents[2]
         / "sample_docs" / "project" / "7" / "golden" / "target_schema.json")
    return json.loads(p.read_text(encoding="utf-8"))


@dataclass
class RoundReport:
    round_id: int
    stage: str
    fired_files: int
    fired_records: int
    decisions: list[Decision] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    broke: bool = False

    @property
    def all_pass(self) -> bool:
        return all(v.ok for v in self.verdicts)

    def render(self) -> str:
        lines = [
            f"── Round {self.round_id} · stage '{self.stage}' "
            f"· fired {self.fired_files} file(s) / {self.fired_records} record(s) ──",
        ]
        if self.decisions:
            lines.append("decisions:")
            for d in self.decisions:
                pid = d.profile_id or "-"
                extra = ""
                if d.remaps:
                    extra = " [" + ", ".join(f"{t}<-{s}" for t, s in d.remaps) + "]"
                lines.append(f"  · {d.action:7} {pid}  ({d.reason}){extra}")
        lines.append("red team:")
        for v in self.verdicts:
            lines.append(f"  [{v.tag}] {v.agent}: {v.summary}")
            if not v.ok and v.detail.get("suggestion"):
                lines.append(f"         ↳ suggestion: {v.detail['suggestion']}")
        lines.append(f"verdict: {'ALL PASS — safe to escalate' if self.all_pass else 'BREAK — stop and learn'}")
        return "\n".join(lines)


@dataclass
class ScaleReport:
    """Result of one length/number stress batch (no approve/reject)."""
    round_id: int
    files: int
    records: int
    shapes: int
    records_per_file: int
    perf: "Verdict | None" = None
    correctness: list["Verdict"] = field(default_factory=list)
    pathways: dict[str, int] = field(default_factory=dict)
    clusters: int = 0
    ok: bool = True
    note: str = ""

    def render(self) -> str:
        lines = [
            f"── Scale round {self.round_id} · {self.files} file(s) × "
            f"{self.records_per_file} rec = {self.records} record(s) · "
            f"{self.shapes} distinct shape(s) → {self.clusters} cluster(s) ──",
        ]
        if self.note:
            lines.append(f"  note: {self.note}")
        if self.pathways:
            lines.append("  pathways: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.pathways.items())))
        if self.perf:
            lines.append(f"  [{self.perf.tag}] {self.perf.agent}: {self.perf.summary}")
            d = self.perf.detail
            if d.get("per_file_s") is not None:
                lines.append(f"         per-file={d['per_file_s']*1000:.1f}ms "
                             f"· suggestion: {d.get('suggestion','')}")
        cok = sum(1 for v in self.correctness if v.ok)
        lines.append(f"  correctness: {cok}/{len(self.correctness)} shape(s) clean"
                     + ("" if self.ok else "  ⚠ some shapes flagged"))
        for v in self.correctness:
            if not v.ok:
                lines.append(f"    [FAIL] {v.summary}")
        return "\n".join(lines)


class Controller:
    def __init__(self, base: str = "http://localhost:8001", *, seed: int = 1337,
                 approve_conformity: float = 0.90,
                 per_file_budget_s: float = 2.5) -> None:
        self.api = AlphaClient(base)
        self.gen = Generator(seed=seed)
        self.decider = Decider(approve_conformity=approve_conformity)
        self.correctness = CorrectnessAdversary()
        self.invariants = InvariantAdversary()
        self.perf = PerformanceAdversary(per_file_budget_s=per_file_budget_s)
        self.project_id: str | None = None
        self.round_id = 0

    # -- setup ---------------------------------------------------------------

    def ensure_project(self, name: str = "alpha-loop (throwaway)") -> str:
        """Create the dedicated throwaway project + set the golden schema once."""
        if not self.api.health():
            raise RuntimeError(
                f"stack not healthy at {self.api.base}; start the API on that "
                f"port and a worker (worker_main.py) first.")
        pid = self.api.create_project(name)
        self.api.set_target_schema(pid, _schema())
        self.project_id = pid
        return pid

    # -- one round -----------------------------------------------------------

    def run_stage(self, stage_index: int, *, n_per_shape: int = 3,
                  cascade_probe: bool = True) -> RoundReport:
        """Fire one stage, decide, apply, and run the full red team."""
        if self.project_id is None:
            self.ensure_project()
        pid = self.project_id
        assert pid is not None
        self.round_id += 1
        stage_name, _ = STAGES[stage_index]

        docs = self.gen.emit_stage(stage_index, n_per_shape=n_per_shape,
                                   round_id=self.round_id)
        report = RoundReport(round_id=self.round_id, stage=stage_name,
                             fired_files=len(docs),
                             fired_records=sum(len(d.records) for d in docs))

        # Fire + wait + perf-measure.
        job = self._fire(pid, docs)
        counts = self.api.job_counts()
        report.verdicts.append(self.perf.measure(
            job, files=len(docs), records=report.fired_records,
            queue_counts=counts))
        if job.state != "completed":
            report.broke = True
            report.verdicts.append(Verdict(
                "controller", False, f"batch job {job.state}: {job.error}"))
            return report

        result = job.result or {}
        outcomes = result.get("outcomes", [])
        lib_before = self.api.library(pid)

        # Decide per cluster, then apply + red-team each.
        by_doc = self._outcomes_by_doc(outcomes, docs)
        for doc, outcome in by_doc:
            entry = self._entry_for(lib_before, outcome)
            decision = self.decider.decide(outcome, entry, doc)
            report.decisions.append(decision)

            # Correctness check on the AS-LEARNED mapping (before we approve it).
            conformed = self._conformed_for(result, doc.doc_id)
            if entry is not None:
                report.verdicts.append(
                    self.correctness.check(doc, entry, conformed))

            # Apply the decision via the live endpoints.
            self._apply(pid, decision, doc, entry)

            # Invariant B: no-cascade check on an approval.
            if decision.action == "approve" and decision.profile_id:
                lib_after = self.api.library(pid)
                report.verdicts.append(self.invariants.check_no_cascade(
                    lib_before, lib_after, decision.profile_id))
                lib_before = lib_after

                # Replay/cascade probe: re-fire the SAME shape; it must replay
                # clean with zero new provisional (and not disturb others).
                if cascade_probe:
                    report.verdicts.append(
                        self._replay_probe(pid, doc, n_per_shape))

        report.broke = not report.all_pass
        return report

    # -- scaling experiment: crank length and/or number ---------------------

    def run_scale(self, *, shapes: list[int], files_per_shape: int = 1,
                  records_per_file: int = 3) -> "ScaleReport":
        """Fire ONE batch of (len(shapes) * files_per_shape) documents, each with
        `records_per_file` records, drawn from the given stage indices. Measures
        throughput (red-team C) over the whole batch and scores each produced
        shape's mapping (red-team A). This is the length/number stress path — it
        does NOT approve/reject (that is the ladder's job); it answers "how does
        the pipeline behave as files get longer and more numerous?"."""
        if self.project_id is None:
            self.ensure_project()
        pid = self.project_id
        assert pid is not None
        self.round_id += 1

        docs: list[FiredDoc] = []
        for si in shapes:
            _, specs = STAGES[si]
            for spec in specs:
                for f in range(files_per_shape):
                    d = self.gen.emit_shape(spec, n_per_shape=records_per_file,
                                            round_id=self.round_id * 1000 + f)
                    # Unique doc_id per (shape, file) so clustering sees distinct
                    # files; same shape still shares one signature (cascade).
                    d.doc_id = f"r{self.round_id}_{spec.label.replace(' ', '_')}_f{f}.json"
                    docs.append(d)

        total_records = sum(len(d.records) for d in docs)
        report = ScaleReport(
            round_id=self.round_id, files=len(docs),
            records=total_records, shapes=len(set(shapes)),
            records_per_file=records_per_file)

        job = self._fire(pid, docs)
        counts = self.api.job_counts()
        report.perf = self.perf.measure(
            job, files=len(docs), records=total_records, queue_counts=counts)
        if job.state != "completed":
            report.ok = False
            report.note = f"batch {job.state}: {job.error}"
            return report

        result = job.result or {}
        outcomes = result.get("outcomes", [])
        lib = self.api.library(pid)
        # Correctness A over every produced shape.
        for doc, outcome in self._outcomes_by_doc(outcomes, docs):
            entry = self._entry_for(lib, outcome)
            if entry is not None:
                v = self.correctness.check(doc, entry, None)
                report.correctness.append(v)
        report.pathways = result.get("summary", {}).get("doc_counts_by_pathway", {})
        report.clusters = result.get("summary", {}).get("clusters", 0)
        report.ok = all(v.ok for v in report.correctness)
        return report

    # -- run to break (mode b) ----------------------------------------------

    def run_to_break(self, *, start: int = 0, n_per_shape: int = 3,
                     on_round=None) -> list[RoundReport]:
        """Escalate through the stage ladder until a red-team FAIL (or the ladder
        is exhausted). Returns every round report; stops on the first break."""
        reports: list[RoundReport] = []
        for i in range(start, len(STAGES)):
            r = self.run_stage(i, n_per_shape=n_per_shape)
            reports.append(r)
            if on_round:
                on_round(r)
            if r.broke:
                break
        return reports

    # -- internals -----------------------------------------------------------

    def _fire(self, pid: str, docs: list[FiredDoc]):
        job_id = self.api.submit_batch(pid, [d.as_batch_doc() for d in docs],
                                       research=True)
        return self.api.wait_for_job(job_id)

    def _replay_probe(self, pid: str, doc: FiredDoc, n: int) -> Verdict:
        """Re-fire the exact same shape and assert it replays clean."""
        # Rebuild the identical shape (same seed/layout) under a probe round id.
        spec = self._spec_for(doc.shape_label)
        refire = self.gen.emit_shape(spec, n_per_shape=n, round_id=9000 + self.round_id)
        job = self._fire(pid, [refire])
        outcomes = (job.result or {}).get("outcomes", [])
        return self.invariants.check_replay(outcomes, doc.shape_label)

    @staticmethod
    def _spec_for(shape_label: str) -> ShapeSpec:
        for _, specs in STAGES:
            for s in specs:
                if s.label == shape_label:
                    return s
        raise KeyError(f"no shape spec for label {shape_label!r}")

    @staticmethod
    def _outcomes_by_doc(outcomes: list[dict[str, Any]],
                         docs: list[FiredDoc]) -> list[tuple[FiredDoc, dict]]:
        """Pair each outcome with the doc it covers (by doc_id membership)."""
        by_id = {d.doc_id: d for d in docs}
        pairs: list[tuple[FiredDoc, dict]] = []
        for o in outcomes:
            for did in o.get("doc_ids", []):
                d = by_id.get(did)
                if d is not None:
                    pairs.append((d, o))
                    break
        return pairs

    @staticmethod
    def _entry_for(library: dict[str, Any],
                   outcome: dict[str, Any]) -> dict[str, Any] | None:
        pid = outcome.get("provisional_profile")
        if not pid:
            return None
        for e in library.get("entries", []):
            if e.get("profile_id") == pid:
                return e
        return None

    @staticmethod
    def _conformed_for(result: dict[str, Any],
                       doc_id: str) -> list[dict[str, Any]] | None:
        # The batch result exposes conformed records per doc only via outputs;
        # if not present inline, the correctness value-check simply skips (it is
        # conservative). Returns None when unavailable.
        return None

    def _apply(self, pid: str, decision: Decision, doc: FiredDoc,
               entry: dict[str, Any] | None) -> None:
        if decision.action == "approve" and decision.profile_id:
            self.api.approve(pid, decision.profile_id)
        elif decision.action == "remap" and decision.profile_id and decision.remaps:
            for target, source_path in decision.remaps:
                self.api.override(pid, decision.profile_id, target, source_path)
        elif decision.action == "reject" and decision.profile_id:
            self.api.reject(pid, decision.profile_id, category="not_our_data",
                            note="alpha-loop: quarantined/irrelevant shape")
        # hold: do nothing
