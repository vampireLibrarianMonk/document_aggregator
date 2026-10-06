"""The Coordinator: the JEV-style command center.

Decomposes a project's correction work into a bounded sub-task DAG, dispatches
each task to the sub-agent that handles its kind (via the parallel queue +
deterministic assembler), threads the results through a shared `artifacts`
context, and iterates to convergence (needs_review -> 0, conflicts preserved,
capped rounds). Every stage's material and every round's state is captured for
the staged-evolution review (Task 4).

The DAG (dependencies in parentheses):
    derive_template        (-)
    extract_draft          (derive_template)
    derive_manifest        (derive_template, extract_draft)   [strategy-driven]
    parse_corrections      (derive_manifest)
    reconcile              (all)
Within a round, independent tasks at the same dependency level can run in
parallel; the assembler keeps assembly deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tests.command_center.convergence import RoundState, assess, count_states
from tests.command_center.core import QueueAssembler, SubAgent, SubTask, TaskResult

# The static plan. `order` is the stable assembly key.
PLAN = [
    SubTask(id="t0_template", kind="derive_template", order=0),
    SubTask(id="t1_draft", kind="extract_draft", order=1, depends_on=("t0_template",)),
    SubTask(id="t2_manifest", kind="derive_manifest", order=2,
            depends_on=("t0_template", "t1_draft")),
    SubTask(id="t3_corrections", kind="parse_corrections", order=3,
            depends_on=("t2_manifest",)),
    SubTask(id="t4_reconcile", kind="reconcile", order=4,
            depends_on=("t0_template", "t1_draft", "t2_manifest", "t3_corrections")),
]

# Which artifact key each task result populates in the shared context.
_ARTIFACT_KEY = {
    "derive_template": "template",
    "extract_draft": "draft",
    "derive_manifest": "manifest",
    "parse_corrections": "corrections",
    "reconcile": "report",
}


@dataclass
class RunRecord:
    """Everything observable about one command-center run (for review)."""
    project_id: str
    pathway: str                      # deterministic | governed
    agent_type: str                   # deterministic | model
    strategy: str                     # S1 | S2 | S3
    rounds: list[RoundState] = field(default_factory=list)
    stage_snapshots: list[dict] = field(default_factory=list)   # per-round artifacts summary
    task_log: list[dict] = field(default_factory=list)          # per-task agent/ok/note
    final_report: dict | None = None
    model_stats: dict | None = None
    error: str = ""


class Coordinator:
    def __init__(self, agents: list[SubAgent], *, parallel: bool = False,
                 max_rounds: int = 4, manifest_strategy=None) -> None:
        self.agents = agents
        self.queue = QueueAssembler(parallel=parallel)
        self.max_rounds = max_rounds
        self.manifest_strategy = manifest_strategy

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
        deterministically, and populate artifacts as we go."""
        done: set[str] = set()
        remaining = list(PLAN)
        while remaining:
            ready = [t for t in remaining if set(t.depends_on) <= done]
            if not ready:
                raise RuntimeError("dependency cycle / unsatisfiable plan")
            results = self.queue.run_batch(ready, context, self._dispatch)
            for r in results:
                record.task_log.append({"task": r.task_id, "agent": r.agent,
                                        "ok": r.ok, "note": r.note, "error": r.error})
                if r.ok:
                    context["artifacts"][_ARTIFACT_KEY[r.kind]] = r.output
                done.add(r.task_id)
            remaining = [t for t in remaining if t.id not in done]
        return context["artifacts"].get("report", {})

    def run(self, raw, *, pathway: str, agent_type: str, strategy: str) -> RunRecord:
        record = RunRecord(project_id=raw.project_id, pathway=pathway,
                           agent_type=agent_type, strategy=strategy)
        context = {
            "raw": raw,
            "artifacts": {},
            "manifest_strategy": self.manifest_strategy,
            "prior_report": None,
            "pathway": pathway,
        }
        prev_nr: int | None = None
        try:
            for rnd in range(self.max_rounds):
                report = self._run_dag_once(context, record)
                state = assess(report, prev_nr)
                state.round = rnd
                record.rounds.append(state)
                record.stage_snapshots.append(self._snapshot(context["artifacts"], rnd))
                record.final_report = report
                # Feed this round's report back so a next round could refine
                # (e.g. a model agent re-examines needs_review units).
                context["prior_report"] = report
                if state.converged or not state.progressed:
                    break
                prev_nr = state.needs_review
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"[:200]
        # Collect model stats if a model agent is present.
        for a in self.agents:
            mc = getattr(a, "mc", None)
            if mc is not None:
                record.model_stats = mc.stats.as_dict()
        return record

    @staticmethod
    def _snapshot(artifacts: dict, rnd: int) -> dict:
        """A compact, reviewable summary of the materials this round produced."""
        tmpl = artifacts.get("template", {})
        manifest = artifacts.get("manifest", {})
        draft = artifacts.get("draft", {})
        corrections = artifacts.get("corrections", [])
        report = artifacts.get("report", {})
        return {
            "round": rnd,
            "template": {
                "sections": [s.get("key") for s in tmpl.get("required_sections", [])],
                "has_furniture": bool(tmpl.get("furniture")),
                "table_specs": list((tmpl.get("table_specs") or {}).keys()),
            },
            "manifest": {
                "fields": [f"{f['section']}.{f['key']}" for f in manifest.get("fields", [])],
                "section_bodies": list(manifest.get("section_bodies", {}).keys()),
                "has_table": bool(manifest.get("table")),
            },
            "draft_sections": [s.get("key") for s in draft.get("sections", [])],
            "corrections": [{"target": c.get("target"), "op": c.get("operation"),
                             "new_value": c.get("new_value")} for c in corrections],
            "report_states": count_states(report) if report else {},
        }
