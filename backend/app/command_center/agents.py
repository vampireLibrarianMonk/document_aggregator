"""Production sub-agents for the command center.

The correction flow, decomposed into bounded tasks the Coordinator dispatches.
Unlike the bake-off agents (which reconstructed structure from raw documents to
test raw-upload fidelity), the production agent orchestrates the SAME pipeline
the direct `run_reconciliation` path uses: it loads the project's structured
artifacts, refines the corrections (figure grounding + optional model, via
app.corrections.refine), and runs the authoritative reconcile engine. So the
coordinator path and the direct path call identical production code — the
coordinator only changes HOW the steps are organized (DAG + queue + convergence
loop), never WHAT the engine computes.

Task kinds:
  load_artifacts     load template / manifest / graphics / corpus / first_attempt
  parse_corrections  load corrections (+ extras) and refine them (grounding +
                     optional model), the no-fabrication gate lives here
  reconcile          run the deterministic engine -> CorrectedReport dict

The engine stays authoritative; the agent never fabricates and never resolves a
genuine conflict.
"""
from __future__ import annotations

from typing import Any

from .core import SubTask, TaskResult

# Task kinds this agent handles (the production correction DAG).
CORRECTION_KINDS = {"load_artifacts", "parse_corrections", "reconcile"}


class CorrectionAgent:
    """Runs the production correction steps against a project's structured data.

    `context` carries the run parameters under `context["params"]`:
        project_id, mode ('draft'|'template'), source_format, extra_corrections
    and accumulates intermediate materials under `context["artifacts"]`.
    """
    name = "correction"

    def can_handle(self, kind: str) -> bool:
        return kind in CORRECTION_KINDS

    def run(self, task: SubTask, context: dict) -> TaskResult:
        try:
            out: Any
            if task.kind == "load_artifacts":
                out = self._load_artifacts(context)
            elif task.kind == "parse_corrections":
                out = self._parse_corrections(context)
            elif task.kind == "reconcile":
                out = self._reconcile(context)
            else:
                return TaskResult(task.id, task.kind, task.order, ok=False,
                                  error=f"unhandled kind {task.kind}", agent=self.name)
            return TaskResult(task.id, task.kind, task.order, output=out, agent=self.name)
        except Exception as exc:  # noqa: BLE001 - record, never crash the queue
            return TaskResult(task.id, task.kind, task.order, ok=False,
                              error=f"{type(exc).__name__}: {exc}"[:200], agent=self.name)

    # -- steps (each mirrors a slice of run_reconciliation) -----------------

    @staticmethod
    def _load_artifacts(context: dict) -> dict:
        from .. import project as sc

        p = context["params"]
        pid, mode, source_format = p["project_id"], p["mode"], p["source_format"]
        template = sc.load_template(pid)
        # When the source is a real document, divine the discipline rubric from
        # the template DOCUMENT's own formatting (same as the direct path).
        if source_format in ("docx", "pdf"):
            tev = sc.load_template_evidence(pid)
            if tev:
                template = {**template, "_evidence": tev}
        artifacts = {
            "template": template,
            "manifest": sc.load_manifest(pid),
            "graphics": sc.load_graphics(pid),
            "corpus": sc.load_corpus(pid),
            "first_attempt": sc._first_attempt_for(pid, mode, source_format),
            "feedback_texts": sc.load_correction_feedback(pid),
        }
        context["artifacts"].update(artifacts)
        return {k: _summarize(k, v) for k, v in artifacts.items()}

    @staticmethod
    def _parse_corrections(context: dict) -> list[dict]:
        from .. import project as sc
        from ..corrections.refine import refine_corrections

        p = context["params"]
        a = context["artifacts"]
        corrections = sc.load_corrections(p["project_id"])
        if p.get("extra_corrections"):
            corrections = corrections + list(p["extra_corrections"])
        corrections = refine_corrections(
            corrections,
            manifest=a["manifest"],
            template=a["template"],
            graphics=a["graphics"],
            feedback_texts=a["feedback_texts"],
            corpus=a["corpus"],
        )
        context["artifacts"]["corrections"] = corrections
        return corrections

    @staticmethod
    def _reconcile(context: dict) -> dict:
        from ..reconcile import reconcile

        a = context["artifacts"]
        report = reconcile(
            first_attempt=a["first_attempt"],
            corpus=a["corpus"],
            graphics_manifest=a["graphics"],
            corrections=a["corrections"],
            template=a["template"],
            project=a["manifest"],
        )
        result = report.model_dump()
        context["artifacts"]["report"] = result
        return result


def _summarize(key: str, value) -> int | bool | str:
    """A compact, loggable summary of a loaded artifact (not the full payload)."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return bool(value)
