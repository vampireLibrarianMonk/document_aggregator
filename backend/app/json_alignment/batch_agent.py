"""Batch pathway coordinator: RUN the per-cluster decisions plan_batch produced,
through the generic command center, concurrently and without fabrication.

plan_batch (batch.py) is pure policy — it decides a pathway per cluster but runs
no conversions. This module executes those decisions:

  replay_clean      run the frozen approved profile over the cluster (no
                    inference) -> conformed golden records + provenance.
  drift_repair      the approved profile no longer fits; re-infer a mapping for
                    THIS cluster (research), register it as a PROVISIONAL new
                    version for human re-approval. Still produces best-effort
                    grounded output for the fields that do map; broken fields
                    surface as needs_review (never fabricated).
  novel_research    no approved profile; infer a mapping, register a PROVISIONAL
                    profile for one-time human approval, and produce grounded
                    output for what maps.
  review            relevance uncertain; produce NOTHING, carry the reason for a
                    human to confirm the cluster is ours.
  reject_irrelevant quarantine; produce NOTHING, carry the reason. The golden
                    shape is never emitted for a rejected cluster.

Concurrency: clusters are independent, so they run on a thread pool sized by
concurrency.worker_count (cores-1, cgroup-aware), capped at the cluster count.
A deterministic path (run_batch) and the command-center path
(run_batch_via_coordinator) produce identical outcomes.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Any

from ..command_center.core import SubTask, TaskResult
from .batch import (
    PATHWAY_DRIFT_REPAIR,
    PATHWAY_NOVEL,
    PATHWAY_REJECT,
    PATHWAY_REPLAY,
    PATHWAY_REVIEW,
    BatchReport,
    Cluster,
    ClusterDecision,
    SourceDoc,
    cluster_documents,
    plan_batch,
)
from .concurrency import worker_count
from .drift import FillBaseline
from .pipeline import AlignmentResult, run_alignment
from .profile_store import STATE_PROVISIONAL, ProfileLibrary, build_profile
from .signature import signature_of
from .source_profile import profile_source
from .target_profile import extract_target

# Pathways that actually emit golden records (vs quarantine/review which do not).
_PRODUCING = {PATHWAY_REPLAY, PATHWAY_DRIFT_REPAIR, PATHWAY_NOVEL}


@dataclass
class ClusterOutcome:
    """The result of executing one cluster's chosen pathway."""
    cluster_key: str
    pathway: str
    doc_ids: tuple[str, ...]
    records: list[dict[str, Any]] = field(default_factory=list)
    conformed: int = 0                       # records that validated clean
    needs_review: int = 0                    # records with any validation issue
    quarantined: bool = False
    provisional_profile: str | None = None   # id registered for human approval
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_key": self.cluster_key,
            "pathway": self.pathway,
            "doc_ids": list(self.doc_ids),
            "produced": len(self.records),
            "conformed": self.conformed,
            "needs_review": self.needs_review,
            "quarantined": self.quarantined,
            "provisional_profile": self.provisional_profile,
            "reasons": list(self.reasons),
        }


def _profile_id_for(cluster: Cluster, target_title: str) -> str:
    """A stable provisional profile id derived from the shape hash."""
    return f"{target_title}__shape_{cluster.key[:10]}"


def _learn_profile(cluster: Cluster, target_schema: dict[str, Any],
                   library: ProfileLibrary, *, pid: str) -> AlignmentResult:
    """Infer a mapping for a cluster, register it as a PROVISIONAL profile (with
    a fill-rate baseline), and return the alignment result. The human approves it
    later; until then it does not participate in approved-only routing."""
    result = run_alignment(cluster.records, target_schema,
                           source_descriptions=cluster.descriptions or None)
    src = profile_source(cluster.records, cluster.descriptions or None)
    target = extract_target(target_schema)
    baseline = FillBaseline.from_provenance(result.provenance)
    profile = build_profile(pid, src, target, result.mapping,
                            meta={"fill_baseline": baseline.to_dict()})
    library.register(pid, signature_of(cluster.records), profile,
                     state=STATE_PROVISIONAL)
    return result


def _outcome_from_result(decision: ClusterDecision, result: AlignmentResult,
                         *, provisional: str | None,
                         extra_reasons: tuple[str, ...] = ()) -> ClusterOutcome:
    conformed = sum(1 for v in result.validation if v.ok)
    return ClusterOutcome(
        cluster_key=decision.cluster_key, pathway=decision.pathway,
        doc_ids=decision.doc_ids, records=result.records,
        conformed=conformed, needs_review=len(result.validation) - conformed,
        provisional_profile=provisional,
        reasons=decision.reasons + extra_reasons,
    )


def execute_cluster(decision: ClusterDecision, cluster: Cluster,
                    target_schema: dict[str, Any], library: ProfileLibrary,
                    ) -> ClusterOutcome:
    """Run a single cluster's pathway. Pure wrt everything except `library`,
    into which novel/drift pathways register a provisional profile."""
    target_title = extract_target(target_schema).title

    if decision.pathway == PATHWAY_REPLAY:
        entry = library.get(decision.matched_profile or "")
        if entry is None:  # defensive: approved profile vanished -> treat as novel
            pid = _profile_id_for(cluster, target_title)
            res = _learn_profile(cluster, target_schema, library, pid=pid)
            return _outcome_from_result(decision, res, provisional=pid,
                                        extra_reasons=("replay target missing; "
                                                       "re-learned provisionally",))
        res = run_alignment(cluster.records, target_schema,
                            profile=entry.profile,
                            source_descriptions=cluster.descriptions or None)
        return _outcome_from_result(decision, res, provisional=None)

    if decision.pathway in (PATHWAY_NOVEL, PATHWAY_DRIFT_REPAIR):
        # Both re-research the cluster; drift_repair additionally supersedes the
        # matched profile's id (version bump) so the human re-approves the delta.
        if decision.pathway == PATHWAY_DRIFT_REPAIR and decision.matched_profile:
            pid = decision.matched_profile
        else:
            pid = _profile_id_for(cluster, target_title)
        res = _learn_profile(cluster, target_schema, library, pid=pid)
        return _outcome_from_result(decision, res, provisional=pid)

    # review / reject: nothing is produced; the golden is never emitted.
    return ClusterOutcome(
        cluster_key=decision.cluster_key, pathway=decision.pathway,
        doc_ids=decision.doc_ids, records=[], conformed=0, needs_review=0,
        quarantined=(decision.pathway == PATHWAY_REJECT),
        reasons=decision.reasons,
    )


@dataclass
class BatchResult:
    """Everything observable about one batch run."""
    report: BatchReport
    outcomes: list[ClusterOutcome] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        produced = sum(len(o.records) for o in self.outcomes)
        conformed = sum(o.conformed for o in self.outcomes)
        needs_review = sum(o.needs_review for o in self.outcomes)
        quarantined_docs = sum(len(o.doc_ids) for o in self.outcomes if o.quarantined)
        provisional = sorted({o.provisional_profile for o in self.outcomes
                              if o.provisional_profile})
        return {
            "clusters": len(self.outcomes),
            "doc_counts_by_pathway": self.report.doc_counts(),
            "produced_records": produced,
            "conformed_records": conformed,
            "needs_review_records": needs_review,
            "quarantined_docs": quarantined_docs,
            "provisional_profiles_awaiting_approval": provisional,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "outcomes": [o.to_dict() for o in self.outcomes],
            "report": self.report.to_dict(),
        }


def run_batch(docs: list[SourceDoc], target_schema: dict[str, Any],
              library: ProfileLibrary, *, reject_below: float,
              research_available: bool = True, parallel: bool = True,
              ) -> BatchResult:
    """Direct (non-coordinator) batch run: plan -> execute each cluster.

    Clusters are independent and run concurrently (thread pool, cores-1) when
    `parallel`; results are reassembled in deterministic cluster order so the
    output does not depend on finish order. This is the reference the
    command-center path must match."""
    clusters = cluster_documents(docs)
    report = plan_batch(docs, target_schema, library, reject_below=reject_below,
                        research_available=research_available)
    by_key = {c.key: c for c in clusters}
    decisions = report.decisions

    def _run(d: ClusterDecision) -> ClusterOutcome:
        return execute_cluster(d, by_key[d.cluster_key], target_schema, library)

    # Profile registration mutates `library`; a thread pool makes that
    # interleave. Registration is per-distinct-id and idempotent-ish, but to
    # keep the library deterministic we execute the PRODUCING/learning pathways
    # that touch the library sequentially, and only parallelize pure replays.
    learn = [d for d in decisions if d.pathway in (PATHWAY_NOVEL, PATHWAY_DRIFT_REPAIR)]
    pure = [d for d in decisions if d.pathway not in (PATHWAY_NOVEL, PATHWAY_DRIFT_REPAIR)]
    outcomes: dict[str, ClusterOutcome] = {}

    if parallel and len(pure) > 1:
        workers = worker_count(maximum=len(pure))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for d, o in zip(pure, ex.map(_run, pure)):
                outcomes[d.cluster_key] = o
    else:
        for d in pure:
            outcomes[d.cluster_key] = _run(d)
    for d in learn:  # sequential: deterministic library mutation
        outcomes[d.cluster_key] = _run(d)

    # Reassemble in the deterministic decision order.
    ordered = [outcomes[d.cluster_key] for d in decisions]
    return BatchResult(report=report, outcomes=ordered)


# -- command-center integration ---------------------------------------------

BATCH_KINDS = {"plan", "execute"}

BATCH_ALIGNMENT_PLAN = [
    SubTask(id="b0_plan", kind="plan", order=0),
    SubTask(id="b1_execute", kind="execute", order=1, depends_on=("b0_plan",)),
]


class BatchAlignmentAgent:
    """Drives the batch pathways through the generic command center.

    context['params'] carries: docs, target_schema, library, reject_below,
    research_available, parallel. Stages accumulate under context['artifacts']."""
    name = "batch_alignment"

    def can_handle(self, kind: str) -> bool:
        return kind in BATCH_KINDS

    def run(self, task: SubTask, context: dict) -> TaskResult:
        try:
            if task.kind == "plan":
                out = self._plan(context)
            elif task.kind == "execute":
                out = self._execute(context)
            else:
                return TaskResult(task.id, task.kind, task.order, ok=False,
                                  error=f"unhandled kind {task.kind}", agent=self.name)
            return TaskResult(task.id, task.kind, task.order, output=out,
                              agent=self.name)
        except Exception as exc:  # noqa: BLE001 - record, never crash the queue
            return TaskResult(task.id, task.kind, task.order, ok=False,
                              error=f"{type(exc).__name__}: {exc}"[:200],
                              agent=self.name)

    def _plan(self, context: dict) -> dict:
        p = context["params"]
        report = plan_batch(p["docs"], p["target_schema"], p["library"],
                            reject_below=p["reject_below"],
                            research_available=p.get("research_available", True))
        context["artifacts"]["batch_report"] = report
        return {"clusters": len(report.decisions),
                "doc_counts": report.doc_counts()}

    def _execute(self, context: dict) -> dict:
        p = context["params"]
        result = run_batch(p["docs"], p["target_schema"], p["library"],
                           reject_below=p["reject_below"],
                           research_available=p.get("research_available", True),
                           parallel=p.get("parallel", True))
        context["artifacts"]["batch_result"] = result
        # Expose a convergence-report shape so the Coordinator's assess() can
        # read the batch like a correction run (needs_review across clusters).
        context["artifacts"]["report"] = _batch_convergence_report(result)
        return result.summary()


def _batch_convergence_report(result: BatchResult) -> dict:
    """Project batch outcomes onto the CorrectedReport shape convergence.assess
    understands: one 'field' per cluster, status reflecting its disposition."""
    fields = []
    for o in result.outcomes:
        if o.quarantined:
            status = "conflict"            # unresolved + not convertible
        elif o.pathway == PATHWAY_REVIEW or o.provisional_profile:
            status = "needs_review"        # awaits human approval/confirmation
        else:
            status = "corrected"           # replayed clean
        fields.append({"key": o.cluster_key, "status": status})
    return {"sections": [{"fields": fields, "graphics": [], "tables": []}],
            "furniture": {"elements": [], "cross_references": []}}


def run_batch_via_coordinator(docs: list[SourceDoc], target_schema: dict[str, Any],
                              library: ProfileLibrary, *, reject_below: float,
                              research_available: bool = True,
                              parallel: bool = True) -> BatchResult:
    """Run the batch through the command-center Coordinator (same queue +
    assembler + convergence machinery as correction/alignment). Returns the same
    BatchResult the direct run_batch produces."""
    from ..command_center.coordinator import Coordinator

    coord = Coordinator([BatchAlignmentAgent()], plan=BATCH_ALIGNMENT_PLAN,
                        max_rounds=1)
    record = coord.run(
        project_id="json_batch", mode="draft",
        extra_params={"docs": docs, "target_schema": target_schema,
                      "library": library, "reject_below": reject_below,
                      "research_available": research_available,
                      "parallel": parallel},
    )
    if record.error:
        raise RuntimeError(record.error)
    result = record.artifacts.get("batch_result")
    if result is None:
        raise RuntimeError("batch produced no result")
    return result


__all__ = [
    "BATCH_ALIGNMENT_PLAN",
    "BatchAlignmentAgent",
    "BatchResult",
    "ClusterOutcome",
    "execute_cluster",
    "run_batch",
    "run_batch_via_coordinator",
]
