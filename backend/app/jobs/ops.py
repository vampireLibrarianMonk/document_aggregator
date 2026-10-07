"""Real worker ops — the close-to-the-metal processing steps.

Each op is a small, composable function registered by name; the worker loop
dispatches to them. Ops reuse the existing pipeline modules (project /
reconcile / discipline / layout) so there is one implementation, exercised both
synchronously (API) and asynchronously (worker).

Ops:
  reconcile          run reconciliation for a project/mode/source_format
  converge           run multi-round convergence
  render_geometry    DOCX -> PDF (LibreOffice, gated) -> vector-layout findings
  generate_document  run the governor (decomposed generation) as a background job
  pipeline           the full end-to-end: convert -> reconcile -> discipline (+ geometry)
"""
from __future__ import annotations

from typing import Any

from .worker import register_op


@register_op("reconcile")
def op_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    from .. import project as sc

    return sc.run_reconciliation(
        mode=payload.get("mode", "draft"),
        project_id=payload.get("project_id", sc.DEFAULT_PROJECT),
        source_format=payload.get("source_format"),
        engine=payload.get("engine", "coordinator"),
    )


@register_op("converge")
def op_converge(payload: dict[str, Any]) -> dict[str, Any]:
    from .. import project as sc

    return sc.run_convergence(
        mode=payload.get("mode", "draft"),
        project_id=payload.get("project_id", sc.DEFAULT_PROJECT),
        source_format=payload.get("source_format"),
    )


@register_op("render_geometry")
def op_render_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    """Render the project's generated document to PDF (if LibreOffice present),
    extract element geometry, and inspect it against the discipline layout rules.
    Degrades to a clear 'skipped' result when soffice is unavailable."""
    from pathlib import Path

    from .. import project as sc
    from ..discipline import load_discipline
    from ..discipline.vector import inspect_vector_layout

    sid = payload.get("project_id", sc.DEFAULT_PROJECT)
    mode = payload.get("mode", "draft")
    fmt = payload.get("source_format", "docx")
    subdir = "template" if mode == "template" else "first_attempt"
    gen = sc._dir(sid) / subdir / "generated" / f"{mode}.{fmt}"
    if not gen.exists():
        return {"ran": False, "reason": f"no generated {fmt} for {sid}/{mode}", "findings": []}
    discipline = load_discipline(sc.load_template(sid))
    findings, ran = inspect_vector_layout(Path(gen).read_bytes(), fmt, discipline)
    return {"ran": ran, "findings": [f.model_dump() for f in findings],
            "reason": "" if ran else "LibreOffice unavailable; geometry tier skipped"}


@register_op("generate_document")
def op_generate_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the governor (decomposed generation) as a background job and store the
    full progress-event log + run summary as the result, so a poller sees the
    whole trail on completion. Offline by default (deterministic author +
    adjudicator, no Bedrock); payload may request an approved `model`, a
    `per_section` authoring mode, and an `adjudicator`.

    This is the async counterpart of the synchronous SSE endpoint
    (/project/governed/stream): same governor, same events, but backgrounded via
    the job queue and polled through /jobs/{id}. Never raises for a model failure
    -- the governor degrades to the deterministic path and reports fell_back."""
    from ..projectgen.generator import ProjectBrief
    from ..projectgen.governor import (
        make_adjudicator,
        make_section_author,
        run_governed,
    )

    freeform = (payload.get("freeform") or "").strip()
    brief = (ProjectBrief(freeform=freeform) if freeform
             else ProjectBrief(domain=payload.get("domain", ""),
                                 doc_type=payload.get("doc_type", "incident report"),
                                 title=payload.get("title", "")))
    model = payload.get("model") or None
    adj_name = payload.get("adjudicator", "deterministic")
    per_section = bool(payload.get("per_section"))

    # Resolve author + adjudicator; everything degrades to offline/deterministic
    # on any failure so a backgrounded job never hard-fails on model issues.
    client = adapter = None
    author = section_author = None
    author_model = "offline"
    if model:
        try:
            import boto3

            from ..config import settings
            from ..projectgen.model_adapters import adapter_for
            client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
            adapter = adapter_for(model)
            author_model = model
            if per_section:
                section_author = make_section_author(model, client=client, adapter=adapter)
            else:
                from ..projectgen.bedrock_gen import BedrockProjectGenerator
                author = BedrockProjectGenerator(model_id=model)
                author_model = author.model_id
        except Exception:
            author = section_author = None
            author_model = "offline"
    if per_section and section_author is None:
        section_author = make_section_author()  # offline per-section
    adj = make_adjudicator(adj_name, model_id=model, client=client, adapter=adapter)

    res = run_governed(brief, author=author, section_author=section_author,
                       adjudicator=adj, author_model=author_model)
    return {
        "summary": res.summary(),
        "events": [e.as_dict() for e in res.events],
        "project_id": res.project_id,
    }


@register_op("batch_align")
def op_batch_align(payload: dict[str, Any]) -> dict[str, Any]:
    """Mass JSON->golden conversion for a project, run through the command
    center. Clusters the uploaded documents by shape, decides a pathway per
    cluster (replay approved profiles / research novel shapes / repair drift /
    review / quarantine), and executes them concurrently (cores-1).

    Payload:
      project_id   the project whose golden target schema + profile library apply
      docs         [{doc_id, records:[...], descriptions?:{...}}, ...]
      research     whether the research/LLM pathway may try (default True)

    Grows and PERSISTS the project's profile library (novel/drift shapes land as
    PROVISIONAL profiles awaiting human approval), so a later batch of the same
    shape replays once approved. Never fabricates: quarantined/review clusters
    emit no golden records."""
    from ..config import settings
    from ..json_alignment.batch import SourceDoc
    from ..json_alignment.batch_agent import run_batch_via_coordinator
    from ..json_alignment.project_store import (
        load_project_library,
        load_target_schema,
        save_project_library,
    )

    project_id = payload.get("project_id", "")
    schema = load_target_schema(project_id)
    if schema is None:
        return {"ok": False, "error": "no golden target schema set for this project"}

    docs = [
        SourceDoc(doc_id=d.get("doc_id", f"doc_{i}"),
                  records=list(d.get("records", [])),
                  descriptions=dict(d.get("descriptions", {})))
        for i, d in enumerate(payload.get("docs", []))
    ]
    if not docs:
        return {"ok": False, "error": "no documents in batch"}

    reject_below = float(payload.get("reject_below", 0.5))
    research = bool(payload.get("research", settings.BEDROCK_ENABLED))

    library = load_project_library(project_id)
    result = run_batch_via_coordinator(
        docs, schema, library, reject_below=reject_below,
        research_available=research)
    # Persist the grown library (new provisional profiles / version bumps).
    save_project_library(project_id, library)

    return {"ok": True, **result.to_dict()}


@register_op("pipeline")
def op_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    """Full end-to-end for a project document source: reconcile (which already
    merges structural + gated geometric discipline findings) and report the
    summary. This is what the async submit endpoint runs."""
    from .. import project as sc

    report = sc.run_reconciliation(
        mode=payload.get("mode", "draft"),
        project_id=payload.get("project_id", sc.DEFAULT_PROJECT),
        source_format=payload.get("source_format", "docx"),
    )
    return {
        "summary": report.get("summary", {}),
        "discipline_finding_count": len(report.get("discipline_findings", [])),
        "vector_tier_ran": report.get("vector_tier_ran", False),
        "report": report,
    }
