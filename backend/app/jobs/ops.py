"""Real worker ops — the close-to-the-metal processing steps.

Each op is a small, composable function registered by name; the worker loop
dispatches to them. Ops reuse the existing pipeline modules (scenario /
reconcile / discipline / layout) so there is one implementation, exercised both
synchronously (API) and asynchronously (worker).

Ops:
  reconcile          run reconciliation for a scenario/mode/source_format
  converge           run multi-round convergence
  inspect_discipline structural discipline inspection of a converted document
  render_geometry    DOCX -> PDF (LibreOffice, gated) -> vector-layout findings
  pipeline           the full end-to-end: convert -> reconcile -> discipline (+ geometry)
"""
from __future__ import annotations

from typing import Any

from .worker import register_op


@register_op("reconcile")
def op_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    from .. import scenario as sc

    return sc.run_reconciliation(
        mode=payload.get("mode", "draft"),
        scenario_id=payload.get("scenario_id", sc.DEFAULT_SCENARIO),
        source_format=payload.get("source_format"),
    )


@register_op("converge")
def op_converge(payload: dict[str, Any]) -> dict[str, Any]:
    from .. import scenario as sc

    return sc.run_convergence(
        mode=payload.get("mode", "draft"),
        scenario_id=payload.get("scenario_id", sc.DEFAULT_SCENARIO),
        source_format=payload.get("source_format"),
    )


@register_op("render_geometry")
def op_render_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    """Render the scenario's generated document to PDF (if LibreOffice present),
    extract element geometry, and inspect it against the discipline layout rules.
    Degrades to a clear 'skipped' result when soffice is unavailable."""
    from pathlib import Path

    from .. import scenario as sc
    from ..discipline import load_discipline
    from ..discipline.vector import inspect_vector_layout

    sid = payload.get("scenario_id", sc.DEFAULT_SCENARIO)
    mode = payload.get("mode", "draft")
    fmt = payload.get("source_format", "docx")
    gen = sc._dir(sid) / "first_attempt" / "generated" / f"{mode}.{fmt}"
    if not gen.exists():
        return {"ran": False, "reason": f"no generated {fmt} for {sid}/{mode}", "findings": []}
    discipline = load_discipline(sc.load_template(sid))
    findings, ran = inspect_vector_layout(Path(gen).read_bytes(), fmt, discipline)
    return {"ran": ran, "findings": [f.model_dump() for f in findings],
            "reason": "" if ran else "LibreOffice unavailable; geometry tier skipped"}


@register_op("pipeline")
def op_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    """Full end-to-end for a scenario document source: reconcile (which already
    merges structural + gated geometric discipline findings) and report the
    summary. This is what the async submit endpoint runs."""
    from .. import scenario as sc

    report = sc.run_reconciliation(
        mode=payload.get("mode", "draft"),
        scenario_id=payload.get("scenario_id", sc.DEFAULT_SCENARIO),
        source_format=payload.get("source_format", "docx"),
    )
    return {
        "summary": report.get("summary", {}),
        "discipline_finding_count": len(report.get("discipline_findings", [])),
        "vector_tier_ran": report.get("vector_tier_ran", False),
        "report": report,
    }
