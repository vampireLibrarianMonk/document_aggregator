"""Command-center integration for JSON schema-alignment.

Reuses the generic coordinator (app.command_center) to orchestrate the
deterministic alignment pipeline as a sub-task DAG, so the same queue +
deterministic assembler + convergence machinery that runs the correction engine
also runs alignment — no new orchestrator.

Plan (dependencies in parentheses):
    profile_source   (-)                inventory the source batch
    extract_target   (-)                parse the target JSON Schema
    infer_mapping    (profile, extract) resolve source->target (or reuse profile)
    execute          (infer_mapping)    apply mapping + transforms
    validate         (execute)          schema + type + grounding gate

Context carries params under context["params"]:
    records:        list[dict]              source objects
    target_schema:  dict                    target JSON Schema
    profile:        ConversionProfile|None  reuse a learned mapping if given
and accumulates stage outputs under context["artifacts"].
"""
from __future__ import annotations

from typing import Any

from ..command_center.core import SubTask, TaskResult
from .executor import execute_mapping
from .mapping import infer_mapping
from .source_profile import profile_source
from .target_profile import extract_target
from .validator import validate_record

ALIGNMENT_KINDS = {
    "profile_source", "extract_target", "infer_mapping", "execute", "validate",
}

# Stable plan; `order` is the deterministic assembly key.
JSON_ALIGNMENT_PLAN = [
    SubTask(id="a0_profile", kind="profile_source", order=0),
    SubTask(id="a1_target", kind="extract_target", order=1),
    SubTask(id="a2_map", kind="infer_mapping", order=2,
            depends_on=("a0_profile", "a1_target")),
    SubTask(id="a3_execute", kind="execute", order=3, depends_on=("a2_map",)),
    SubTask(id="a4_validate", kind="validate", order=4, depends_on=("a3_execute",)),
]


class JsonAlignmentAgent:
    """Runs the deterministic alignment stages for the coordinator."""
    name = "json_alignment"

    def can_handle(self, kind: str) -> bool:
        return kind in ALIGNMENT_KINDS

    def run(self, task: SubTask, context: dict) -> TaskResult:
        try:
            out: Any
            if task.kind == "profile_source":
                out = self._profile(context)
            elif task.kind == "extract_target":
                out = self._target(context)
            elif task.kind == "infer_mapping":
                out = self._map(context)
            elif task.kind == "execute":
                out = self._execute(context)
            elif task.kind == "validate":
                out = self._validate(context)
            else:
                return TaskResult(task.id, task.kind, task.order, ok=False,
                                  error=f"unhandled kind {task.kind}", agent=self.name)
            return TaskResult(task.id, task.kind, task.order, output=out,
                              agent=self.name)
        except Exception as exc:  # noqa: BLE001 - record, never crash the queue
            return TaskResult(task.id, task.kind, task.order, ok=False,
                              error=f"{type(exc).__name__}: {exc}"[:200],
                              agent=self.name)

    def _profile(self, context: dict) -> dict:
        src = profile_source(context["params"]["records"])
        context["artifacts"]["source_profile"] = src
        return {"fields": len(src.fields), "records": src.record_count}

    def _target(self, context: dict) -> dict:
        target = extract_target(context["params"]["target_schema"])
        context["artifacts"]["target"] = target
        return {"title": target.title, "fields": len(target.fields),
                "required": list(target.required_names())}

    def _map(self, context: dict) -> dict:
        a = context["artifacts"]
        profile = context["params"].get("profile")
        mapping = profile.mapping if profile is not None else infer_mapping(
            a["source_profile"], a["target"])
        a["mapping"] = mapping
        return {"mapped": len(mapping.mapped()),
                "needs_review": mapping.needs_review(),
                "conflicts": mapping.conflicts()}

    def _execute(self, context: dict) -> dict:
        a = context["artifacts"]
        records, prov = execute_mapping(
            context["params"]["records"], a["mapping"], a["target"])
        a["records"] = records
        a["provenance"] = prov
        return {"produced": len(records)}

    def _validate(self, context: dict) -> dict:
        a = context["artifacts"]
        reports = [
            validate_record(rec, a["provenance"][i], a["target"])
            for i, rec in enumerate(a["records"])
        ]
        a["validation"] = reports
        invalid = sum(0 if r.ok else 1 for r in reports)
        # Expose a correction-report-shaped view so convergence.assess can read
        # alignment the same way it reads corrections (needs_review/conflict).
        a["report"] = _alignment_report(a["mapping"], reports)
        return {"validated": len(reports), "invalid": invalid}


def run_alignment_via_coordinator(records: list[dict], target_schema: dict, *,
                                  profile: Any = None, parallel: bool = False,
                                  max_rounds: int = 1) -> dict:
    """Drive the deterministic alignment pipeline through the generic command
    center and return the final alignment artifacts.

    Reuses app.command_center exactly as the correction path does — same queue,
    deterministic assembler, and convergence loop — proving the orchestrator is
    workflow-agnostic.

    Alignment is single-pass (max_rounds=1): abstentions here are permanent
    deterministic non-matches, not feedback-resolvable needs_review, so iterating
    cannot change the result. The convergence loop is still honored structurally;
    it simply has nothing to improve across rounds."""
    from ..command_center.coordinator import Coordinator

    coord = Coordinator([JsonAlignmentAgent()], plan=JSON_ALIGNMENT_PLAN,
                        parallel=parallel, max_rounds=max_rounds)
    record = coord.run(
        project_id="json_alignment", mode="draft",
        extra_params={"records": records, "target_schema": target_schema,
                      "profile": profile},
    )
    if record.error:
        raise RuntimeError(record.error)
    a = record.artifacts
    return {
        "record": record,
        "report": record.final_report,
        "records": a.get("records", []),
        "provenance": a.get("provenance", []),
        "validation": a.get("validation", []),
        "mapping": a.get("mapping"),
    }


def _alignment_report(mapping, reports) -> dict:
    """Project alignment status onto the CorrectedReport shape the command
    center's convergence detector understands (one 'field' per target)."""
    fields = []
    for fm in mapping.fields:
        if fm.status == "conflict":
            status = "conflict"
        elif fm.status == "needs_review":
            status = "needs_review"
        else:
            status = "corrected"
        fields.append({"key": fm.target, "status": status})
    return {"sections": [{"fields": fields, "graphics": [], "tables": []}],
            "furniture": {"elements": [], "cross_references": []},
            "invalid_records": sum(0 if r.ok else 1 for r in reports)}


__all__ = [
    "ALIGNMENT_KINDS",
    "JSON_ALIGNMENT_PLAN",
    "JsonAlignmentAgent",
    "run_alignment_via_coordinator",
]
