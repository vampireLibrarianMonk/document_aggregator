"""The Coordinator: the production command center.

Decomposes a correction run into a bounded sub-task DAG, dispatches each task to
the sub-agent that handles its kind (through the parallel queue + deterministic
assembler), threads results through a shared `artifacts` context, and iterates
to convergence (needs_review -> 0, conflicts preserved, capped rounds).

Production correction DAG (dependencies in parentheses):
    load_artifacts     (-)
    parse_corrections  (load_artifacts)
    reconcile          (load_artifacts, parse_corrections)

This is intentionally generic: a different workflow supplies its own PLAN + a
SubAgent that handles those kinds, and reuses the same queue/assembler and
convergence machinery.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .convergence import RoundState, assess
from .core import QueueAssembler, SubAgent, SubTask, TaskResult

# The production correction plan. `order` is the stable assembly key.
CORRECTION_PLAN = [
    SubTask(id="t0_artifacts", kind="load_artifacts", order=0),
    SubTask(id="t1_corrections", kind="parse_corrections", order=1,
            depends_on=("t0_artifacts",)),
    SubTask(id="t2_reconcile", kind="reconcile", order=2,
            depends_on=("t0_artifacts", "t1_corrections")),
]

@dataclass
class RunRecord:
    """Everything observable about one command-center run."""
    project_id: str
    mode: str
    rounds: list[RoundState] = field(default_factory=list)
    task_log: list[dict] = field(default_factory=list)
    final_report: dict | None = None
    artifacts: dict = field(default_factory=dict)
    error: str = ""


class Coordinator:
    """Runs a sub-task DAG to convergence against a set of agents."""

    def __init__(self, agents: list[SubAgent], *, plan: list[SubTask] | None = None,
                 parallel: bool = False, max_rounds: int = 4) -> None:
        self.agents = agents
        self.plan = plan if plan is not None else CORRECTION_PLAN
        self.queue = QueueAssembler(parallel=parallel)
        self.max_rounds = max_rounds

    def _agent_for(self, kind: str) -> SubAgent:
        for a in self.agents:
            if a.can_handle(kind):
                return a
        raise ValueError(f"no agent for kind {kind}")

    def _dispatch(self, task: SubTask, context: dict) -> TaskResult:
        return self._agent_for(task.kind).run(task, context)

    def _run_dag_once(self, context: dict, record: RunRecord) -> dict:
        """Execute the plan honoring dependencies: group tasks into dependency
        levels, run each level through the queue (parallel-capable), assemble
        deterministically, and record each result."""
        done: set[str] = set()
        remaining = list(self.plan)
        while remaining:
            ready = [t for t in remaining if set(t.depends_on) <= done]
            if not ready:
                raise RuntimeError("dependency cycle / unsatisfiable plan")
            results = self.queue.run_batch(ready, context, self._dispatch)
            for r in results:
                record.task_log.append({"task": r.task_id, "agent": r.agent,
                                        "ok": r.ok, "note": r.note, "error": r.error})
                if not r.ok:
                    raise RuntimeError(f"task {r.task_id} failed: {r.error}")
                done.add(r.task_id)
            remaining = [t for t in remaining if t.id not in done]
        return context["artifacts"].get("report", {})

    def run(self, *, project_id: str, mode: str = "draft",
            source_format: str | None = None,
            extra_corrections: list[dict] | None = None,
            extra_params: dict | None = None) -> RunRecord:
        record = RunRecord(project_id=project_id, mode=mode)
        params = {
            "project_id": project_id,
            "mode": mode,
            "source_format": source_format,
            "extra_corrections": extra_corrections,
        }
        # Workflow-specific inputs (e.g. alignment's records/target_schema) are
        # merged in without the Coordinator needing to know the workflow.
        if extra_params:
            params.update(extra_params)
        context = {"params": params, "artifacts": {}}
        prev_nr: int | None = None
        try:
            for rnd in range(self.max_rounds):
                report = self._run_dag_once(context, record)
                state = assess(report, prev_nr)
                state.round = rnd
                record.rounds.append(state)
                record.final_report = report
                if state.converged or not state.progressed:
                    break
                prev_nr = state.needs_review
        except Exception as exc:  # noqa: BLE001 - surface as a record error
            record.error = f"{type(exc).__name__}: {exc}"[:200]
        record.artifacts = context["artifacts"]
        return record


def run_correction(project_id: str, *, mode: str = "draft",
                   source_format: str | None = None,
                   extra_corrections: list[dict] | None = None,
                   parallel: bool = False, max_rounds: int = 4) -> dict:
    """Convenience entry: run the production correction DAG for a project and
    return the final CorrectedReport dict (same shape as run_reconciliation).

    The optional vector-layout tier (DOCX/PDF geometry findings) is applied here
    too, so the coordinator path matches the direct path's output exactly.
    """
    from .agents import CorrectionAgent

    coord = Coordinator([CorrectionAgent()], parallel=parallel, max_rounds=max_rounds)
    record = coord.run(project_id=project_id, mode=mode, source_format=source_format,
                       extra_corrections=extra_corrections)
    if record.error:
        raise RuntimeError(record.error)
    result = record.final_report or {}
    # Mirror run_reconciliation's post-reconcile vector-layout tier so the two
    # paths are output-identical.
    if source_format in ("docx", "pdf"):
        from .. import project as sc

        template = sc.load_template(project_id)
        vec = sc._vector_findings(project_id, mode, source_format, template)
        if vec:
            result["discipline_findings"] = result.get("discipline_findings", []) + vec
            result["vector_tier_ran"] = True
    return result


__all__ = [
    "CORRECTION_PLAN",
    "Coordinator",
    "RunRecord",
    "run_correction",
]
