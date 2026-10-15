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

import logging
from typing import Any

from .worker import register_op

_log = logging.getLogger(__name__)


@register_op("reconcile")
def op_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    from .. import project as sc

    return sc.run_reconciliation(
        mode=payload.get("mode", "draft"),
        project_id=payload.get("project_id", sc.DEFAULT_PROJECT),
        source_format=payload.get("source_format"),
        engine=payload.get("engine", "orchestrator"),
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
    from .progress import current_sink, publish_progress
    from ..json_alignment.cascade import schema_fingerprint
    from ..json_alignment.project_store import (
        effective_target_schema,
        load_project_library,
        save_batch_outputs,
        save_pending_ledger,
        save_project_library,
        save_shape_records,
    )

    project_id = payload.get("project_id", "")
    # Use the EFFECTIVE schema: the golden schema with the project's
    # human-accepted value vocabulary merged into each field's taxonomy, so a
    # value a reviewer taught (e.g. platform 'Linux') is allowed on this run.
    schema = effective_target_schema(project_id)
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

    # Per-file progress: total is known up front (one tick-unit per source doc);
    # publish 0/total before the batch so the first poll already shows "0 of M".
    total = len(docs)
    publish_progress(0, total)

    library = load_project_library(project_id)
    result = run_batch_via_coordinator(
        docs, schema, library, reject_below=reject_below,
        research_available=research,
        on_cluster_done=lambda done: publish_progress(done, total))

    # NIT-11: a non-empty batch whose progress never advanced past 0 points at a
    # wiring bug (the callback never fired); warn once. Progress is observational
    # so this never changes the result.
    sink = current_sink()
    if total > 0 and sink is not None and not sink.advanced:
        _log.warning("batch_align: progress never advanced for %d files "
                     "(project_id=%s)", total, project_id)
    # Persist the grown library (new provisional profiles / version bumps).
    save_project_library(project_id, library)

    # Persist the conformed output per SOURCE file so the Report & Export tab can
    # offer per-file "<name>_conformed_<DTG>.json" downloads. Each producing
    # outcome's records are partitioned back to the doc_id they came from.
    pathways_by_doc = {
        did: o.pathway for o in result.outcomes for did in o.doc_ids
    }
    save_batch_outputs(project_id, result.conformed_by_doc(),
                       pathways_by_doc=pathways_by_doc)

    # Retain the RAW source records per learned shape (keyed by profile_id), so a
    # later human field-override can be re-applied over the shape's actual data
    # and report how many records the fix now completes (the propagation report).
    records_by_doc = {d.doc_id: d.records for d in docs}
    records_by_profile: dict[str, list[dict]] = {}
    for o in result.outcomes:
        if o.provisional_profile:
            bucket = records_by_profile.setdefault(o.provisional_profile, [])
            for did in o.doc_ids:
                bucket.extend(records_by_doc.get(did, []))
    if records_by_profile:
        save_shape_records(project_id, records_by_profile)

    # Pending ledger (design §2.1): one entry per input doc with its OWN raw
    # records/descriptions, the cluster it belonged to, a per-doc conformance
    # status, and the provisional profile its own cluster produced (advisory).
    # Best-effort: a failure here must NOT fail a good conversion (§2.9) — the
    # library/outputs are already persisted; the ledger only accelerates a later
    # approve's cascade, which degrades to N==0 when it is absent.
    try:
        kept_by_doc = result.conformed_by_doc()
        # doc_id -> its cluster_key and its own cluster's provisional profile.
        cluster_key_by_doc: dict[str, str] = {}
        produced_profile_by_doc: dict[str, str | None] = {}
        for o in result.outcomes:
            for did in o.doc_ids:
                cluster_key_by_doc[did] = o.cluster_key
                produced_profile_by_doc[did] = o.provisional_profile
        ledger_files = []
        for d in docs:
            input_count = len(d.records)
            kept = len(kept_by_doc.get(d.doc_id, []))
            status = ("conformant" if (input_count > 0 and kept == input_count)
                      else "pending")
            ledger_files.append({
                "doc_id": d.doc_id,
                "records": d.records,
                "descriptions": d.descriptions,
                "cluster_key": cluster_key_by_doc.get(d.doc_id, ""),
                "status": status,
                "produced_cluster_profile": produced_profile_by_doc.get(d.doc_id),
            })
        save_pending_ledger(project_id, {
            "schema_fingerprint": schema_fingerprint(schema),
            "files": ledger_files,
        })
    except Exception:  # noqa: BLE001 - advisory artifact; never fail the batch
        _log.warning("batch_align: pending ledger write failed (project_id=%s)",
                     project_id, exc_info=True)

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
