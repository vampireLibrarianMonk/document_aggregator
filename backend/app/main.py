"""FastAPI surface for the document aggregation platform.

Barebones but complete: projects, document upload + pipeline status, canonical
JSON, supplementals (comments / emails / corrections), aggregated intermediate
report, and export to json/markdown/docx/pptx/pdf.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .aggregate import _sentiment, build_report
from .config import settings
from .exporters import export_report
from .models import Project, Supplemental, SupplementalKind
from .pipeline import ingest_document
from .search import search as run_search
from .store import store

app = FastAPI(title="Document Aggregation Platform", version=settings.PIPELINE_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    return {"status": "ready", "data_dir": str(settings.DATA_DIR)}


@app.get("/diagnostics")
def diagnostics() -> dict:
    """A live snapshot of what the pipeline can actually do (embeddings, OCR,
    LibreOffice geometry tier, Bedrock, versions), so silent degradations are
    visible. Gated by DIAGNOSTICS_ENABLED (off by default)."""
    if not settings.DIAGNOSTICS_ENABLED:
        raise HTTPException(404, "diagnostics is disabled (set DIAGNOSTICS_ENABLED=true to enable)")
    from .diagnostics import collect

    return collect()


@app.get("/config")
def client_config() -> dict:
    """Lightweight, client-facing feature flags the frontend reads on load.
    Cheap (no model load), unlike /diagnostics."""
    return {
        "samples_enabled": bool(settings.SAMPLES_ENABLED),
        "diagnostics_enabled": bool(settings.DIAGNOSTICS_ENABLED),
    }


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


class CreateProject(BaseModel):
    name: str
    description: str = ""


@app.post("/projects")
def create_project(body: CreateProject) -> Project:
    pid = "proj_" + uuid.uuid4().hex[:10]
    return store.create_project(Project(id=pid, name=body.name, description=body.description))


@app.get("/projects")
def list_projects() -> list[Project]:
    """The live project list: ONLY projects the user has actually created in the
    store (by uploading documents, generating, or instantiating a sample case).
    The app starts EMPTY. The bundled sample cases are NOT auto-listed here; they
    are templates surfaced via GET /templates and instantiated on demand."""
    return store.list_projects()


class InstantiateTemplate(BaseModel):
    name: str | None = None  # optional override for the new project's name


def _require_samples() -> None:
    """Guard: the in-app sample feature is opt-in (SAMPLES_ENABLED). When off,
    the sample endpoints behave as if they do not exist, so the feature is fully
    gated server-side, not just hidden in the UI."""
    if not settings.SAMPLES_ENABLED:
        raise HTTPException(
            404, "sample instantiation is disabled (set SAMPLES_ENABLED=true to enable)"
        )


@app.get("/templates")
def list_templates() -> list[dict]:
    """The catalog of bundled sample cases a user can instantiate into a real,
    persisted project on demand. Gated by SAMPLES_ENABLED (off by default):
    samples are normally run from the repo per the user guide. Bundle-only, so
    an already-instantiated copy never shows up here as a duplicate."""
    _require_samples()
    return project.list_templates()


@app.post("/projects/from-template/{case_id}")
def instantiate_template(case_id: str, body: InstantiateTemplate | None = None) -> Project:
    """Create a NEW persisted project by copying a bundled sample case into the
    store. Gated by SAMPLES_ENABLED. The source fixtures in the repo are never
    modified."""
    _require_samples()
    name = body.name if body and body.name else None
    try:
        return store.instantiate_from_template(case_id, name)
    except FileNotFoundError:
        raise HTTPException(404, f"no such sample case: {case_id}")


def _resolve_project(project_id: str) -> Project:
    """Return the project record, materializing one on first use for a project
    id that exists in either store (so selecting a demo project as the active
    project just works). 404 only if neither a project nor a project exists."""
    p = store.get_project(project_id)
    if p:
        return p
    # A project that exists but has no project record yet -> adopt it.
    if (settings.resolve_project_data_dir(project_id) / "project.json").exists():
        title = project_id
        try:
            title = project.load_manifest(project_id).get("title", project_id)
        except Exception:
            pass
        return store.create_project(Project(id=project_id, name=title))
    raise HTTPException(404, "project not found")


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> Project:
    return _resolve_project(project_id)


class UpdateProject(BaseModel):
    """Partial update of a project's editable settings. Only provided fields
    change; others keep their current value."""
    name: str | None = None
    description: str | None = None
    reject_below: float | None = None  # batch relevance dial (0..1)


@app.patch("/projects/{project_id}")
def update_project(project_id: str, body: UpdateProject) -> Project:
    """Update a project's editable settings (name, description, and the batch
    relevance dial `reject_below`). Materializes an adopted demo project first,
    so it works for any project id the app can resolve."""
    proj = _resolve_project(project_id)
    if body.name is not None:
        proj.name = body.name
    if body.description is not None:
        proj.description = body.description
    if body.reject_below is not None:
        # The model validator clamps to [0,1]; re-validate via a round-trip.
        proj = proj.model_copy(update={"reject_below": body.reject_below})
        proj = Project(**proj.model_dump())
    return store.update_project(proj)


@app.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    """Permanently delete a project the user created, with all its data. 404 if
    it does not exist in the store (bundled samples are not deletable; they are
    read-only templates)."""
    if not store.delete_project(project_id):
        raise HTTPException(404, "project not found")
    return {"deleted": project_id}


# --------------------------------------------------------------------------
# Documents + pipeline status
# --------------------------------------------------------------------------

_VALID_KINDS = {"corpus", "template", "corrections", "first_draft"}


@app.post("/projects/{project_id}/documents")
async def upload_document(
    project_id: str, file: UploadFile = File(...), kind: str = Form("corpus")
) -> dict:
    """Upload a document into one of the four Ingestion areas. `kind` tags which
    area it belongs to: corpus | template | corrections | first_draft."""
    _resolve_project(project_id)
    if kind not in _VALID_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_VALID_KINDS)}")
    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"file exceeds {settings.MAX_UPLOAD_MB} MB")
    rec = ingest_document(project_id, file.filename or "unnamed", data, kind=kind)
    return {
        "document_id": rec.id,
        "kind": rec.kind.value,
        "overall_status": rec.overall_status,
        "stages": [s.model_dump() for s in rec.stages],
    }


@app.get("/projects/{project_id}/documents")
def list_documents(project_id: str) -> list[dict]:
    _resolve_project(project_id)
    out = []
    for r in store.list_records(project_id):
        d = r.model_dump()
        d["overall_status"] = r.overall_status
        out.append(d)
    return out


@app.get("/projects/{project_id}/readiness")
def project_readiness(project_id: str) -> dict:
    """Report what has been ingested per area and whether the correction
    pipeline's input prerequisites are satisfied, so the UI can gate the
    Correction Pipeline tab and show the user what is still required.

    The two valid input shapes are:
      A) corpus + template   + corrections
      B) corpus + first_draft + corrections
    So the requirements are: corpus present, corrections present, AND at least
    one of {template, first_draft}.
    """
    _resolve_project(project_id)
    counts = {"corpus": 0, "template": 0, "corrections": 0, "first_draft": 0}
    for r in store.list_records(project_id):
        k = getattr(r.kind, "value", r.kind)
        if k in counts:
            counts[k] += 1
    has_corpus = counts["corpus"] > 0
    has_template = counts["template"] > 0
    has_draft = counts["first_draft"] > 0
    has_corrections = counts["corrections"] > 0

    problems: list[str] = []
    if not has_corpus:
        problems.append("Upload the original corpus (required).")
    if not (has_template or has_draft):
        problems.append("Upload a template or a first draft (at least one is required).")
    if not has_corrections:
        problems.append("Upload corrections — reviewer feedback is required.")

    return {
        "counts": counts,
        "ready": len(problems) == 0,
        "problems": problems,
    }


@app.get("/projects/{project_id}/documents/{document_id}")
def get_document(project_id: str, document_id: str) -> dict:
    r = store.get_record(project_id, document_id)
    if not r:
        raise HTTPException(404, "document not found")
    d = r.model_dump()
    d["overall_status"] = r.overall_status
    return d


@app.get("/projects/{project_id}/documents/{document_id}/canonical")
def get_canonical(project_id: str, document_id: str) -> dict:
    doc = store.get_canonical(project_id, document_id)
    if not doc:
        raise HTTPException(404, "canonical document not found")
    return doc.model_dump()


def _require_doc_bytes(project_id: str, document_id: str):
    """Resolve a document that belongs to the project and return (record, bytes).
    404 if the project, record, or source bytes are missing. This is the single
    path-guard for all read-only preview endpoints."""
    _resolve_project(project_id)
    rec = store.get_record(project_id, document_id)
    if rec is None:
        raise HTTPException(404, "document not found")
    data = store.read_source_bytes(project_id, document_id, rec.filename)
    if data is None:
        raise HTTPException(404, "document source bytes not found")
    return rec, data


@app.get("/projects/{project_id}/documents/{document_id}/raw")
def get_raw(project_id: str, document_id: str) -> dict:
    """Metadata for the Raw view: the original filename/mime, how it should be
    previewed (preview_kind), and the decoded text when it is a text file."""
    from . import preview

    rec, data = _require_doc_bytes(project_id, document_id)
    kind = preview.classify(rec.filename, rec.mime_type)
    out = {
        "filename": rec.filename,
        "mime_type": rec.mime_type,
        "byte_size": len(data),
        "preview_kind": kind,
    }
    if kind == "text":
        try:
            out["is_text"] = True
            out["text"] = data.decode("utf-8")
        except UnicodeDecodeError:
            out["is_text"] = False
            out["text"] = None
            out["preview_kind"] = "unsupported"
    else:
        out["is_text"] = False
        out["text"] = None
    return out


@app.get("/projects/{project_id}/documents/{document_id}/rawfile")
def get_rawfile(project_id: str, document_id: str) -> Response:
    """Stream the ORIGINAL bytes inline for browser-native rendering (images and
    PDF). Read-only: serves the stored immutable source unchanged. Office files
    are NOT served here (use preview.pdf); unknown types are refused."""
    from . import preview

    rec, data = _require_doc_bytes(project_id, document_id)
    kind = preview.classify(rec.filename, rec.mime_type)
    if kind == "image":
        media = preview.image_media_type(rec.filename, rec.mime_type)
    elif kind == "pdf":
        media = "application/pdf"
    else:
        raise HTTPException(415, f"{kind} is not served as a raw file")
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@app.get("/projects/{project_id}/documents/{document_id}/preview.pdf")
def get_preview_pdf(project_id: str, document_id: str) -> Response:
    """Convert an Office document (DOCX/PPTX/...) to PDF and stream it inline for
    preview. Read-only + cached. 415 if the document is not an Office type; 503
    with a clear message when LibreOffice is unavailable."""
    from . import preview

    rec, data = _require_doc_bytes(project_id, document_id)
    if preview.classify(rec.filename, rec.mime_type) != "office-pdf":
        raise HTTPException(415, "document is not an Office type")
    pdf = preview.cached_office_pdf(project_id, document_id, data, preview.office_ext(rec.filename))
    if pdf is None:
        raise HTTPException(
            503, "preview unavailable (LibreOffice not present or conversion failed)"
        )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.delete("/projects/{project_id}/documents/{document_id}")
def delete_document(project_id: str, document_id: str) -> dict:
    """Remove one document from a project (record, canonical, source bytes, and
    its chunks/vectors from the index). 404 if it does not exist."""
    _resolve_project(project_id)
    if not store.delete_document(project_id, document_id):
        raise HTTPException(404, "document not found")
    return {"deleted": document_id}


class MoveDocument(BaseModel):
    target_project_id: str


@app.post("/projects/{project_id}/documents/{document_id}/move")
def move_document(project_id: str, document_id: str, body: MoveDocument) -> dict:
    """Move a document to another project, preserving its area (kind). The
    source bytes are re-ingested into the target (so the target's index is
    correct) and the document is removed from the source project."""
    _resolve_project(project_id)
    if body.target_project_id == project_id:
        raise HTTPException(400, "target project must differ from the current one")
    _resolve_project(body.target_project_id)  # 404 if target does not exist
    rec = store.get_record(project_id, document_id)
    if rec is None:
        raise HTTPException(404, "document not found")
    data = store.read_source_bytes(project_id, document_id, rec.filename)
    if data is None:
        raise HTTPException(404, "document source bytes not found")
    new_rec = ingest_document(body.target_project_id, rec.filename, data, kind=rec.kind.value)
    store.delete_document(project_id, document_id)
    return {"moved": document_id, "to": body.target_project_id, "new_document_id": new_rec.id}


# --------------------------------------------------------------------------
# Supplementals (comments / angry emails / corrections / interview notes)
# --------------------------------------------------------------------------


class CreateSupplemental(BaseModel):
    kind: SupplementalKind
    author: str = "unknown"
    subject: str = ""
    body: str = ""
    target_document_id: str | None = None


@app.post("/projects/{project_id}/supplementals")
def add_supplemental(project_id: str, body: CreateSupplemental) -> Supplemental:
    _resolve_project(project_id)
    supp = Supplemental(
        id="supp_" + uuid.uuid4().hex[:10],
        kind=body.kind,
        author=body.author,
        subject=body.subject,
        body=body.body,
        sentiment=_sentiment(body.subject + " " + body.body),
        target_document_id=body.target_document_id,
    )
    store.save_supplemental(project_id, supp)
    return supp


@app.get("/projects/{project_id}/supplementals")
def list_supplementals(project_id: str) -> list[Supplemental]:
    _resolve_project(project_id)
    return store.list_supplementals(project_id)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


@app.post("/projects/{project_id}/search")
def search_project(project_id: str, body: SearchRequest) -> dict:
    _resolve_project(project_id)
    try:
        return run_search(project_id, body.query, body.top_k)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


# --------------------------------------------------------------------------
# Aggregated intermediate report + export
# --------------------------------------------------------------------------


@app.get("/projects/{project_id}/report")
def get_report(project_id: str) -> dict:
    _resolve_project(project_id)
    return build_report(project_id)


@app.get("/projects/{project_id}/export")
def export(project_id: str, format: str = "json") -> Response:
    _resolve_project(project_id)
    report = build_report(project_id)
    try:
        data, media_type, filename = export_report(report, format)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# Correction project: the four-component pipeline
#   corpus -> first-attempt (draft|template) -> corrections -> corrected JSON
# --------------------------------------------------------------------------

from . import project  # noqa: E402


@app.get("/project-cases")
def project_cases_list() -> list[dict]:
    """The raw list of correction cases (manifest id/title/domain). The unified
    GET /projects is the primary list; this exposes the case-level view."""
    return project.list_project_cases()


# --------------------------------------------------------------------------
# Async jobs: submit work to the worker pool, poll for status/result
# --------------------------------------------------------------------------


class JobRequest(BaseModel):
    op: str
    payload: dict = {}
    priority: int = 100


@app.post("/jobs")
def submit_job(body: JobRequest) -> dict:
    """Enqueue a job for the worker pool. Returns a job_id to poll."""
    from .jobs import ops as _ops  # noqa: F401  (registers ops)
    from .jobs.runtime import get_queue
    from .jobs.worker import registered_ops

    if body.op not in registered_ops():
        raise HTTPException(400, f"unknown op '{body.op}'; known: {registered_ops()}")
    job_id = get_queue().enqueue(body.op, body.payload, priority=body.priority)
    return {"job_id": job_id, "state": "pending"}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    from .jobs.runtime import get_queue

    job = get_queue().get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {
        "job_id": job.id,
        "op": job.op,
        "state": job.state.value,
        "attempts": job.attempts,
        "error": job.error,
        "result": job.result,
    }


@app.get("/jobs")
def job_counts() -> dict:
    from .jobs.runtime import get_queue

    return {"counts": get_queue().counts()}


# --------------------------------------------------------------------------
# Batch JSON -> golden conversion: set the golden schema, submit a batch
# (clustered + routed per shape, async), inspect the learned library, and
# approve a cluster's provisional profile so that shape streamlines later.
# --------------------------------------------------------------------------


class TargetSchemaBody(BaseModel):
    # The golden JSON Schema everything in this project maps to. Named `target`
    # (not `schema`) to avoid shadowing a Pydantic BaseModel attribute.
    target: dict


@app.put("/projects/{project_id}/alignment/target-schema")
def set_target_schema(project_id: str, body: TargetSchemaBody) -> dict:
    """Set (or replace) the project's golden target JSON Schema."""
    _resolve_project(project_id)
    from .json_alignment.project_store import save_target_schema
    from .json_alignment.target_profile import extract_target

    # Validate it parses into a usable target (fields/required) before saving.
    target = extract_target(body.target)
    if not target.fields:
        raise HTTPException(400, "target schema has no top-level properties")
    save_target_schema(project_id, body.target)
    return {"project_id": project_id, "title": target.title,
            "fields": len(target.fields),
            "required": list(target.required_names())}


@app.get("/projects/{project_id}/alignment/target-schema")
def get_target_schema(project_id: str) -> dict:
    _resolve_project(project_id)
    from .json_alignment.project_store import load_target_schema

    schema = load_target_schema(project_id)
    if schema is None:
        raise HTTPException(404, "no golden target schema set for this project")
    return {"project_id": project_id, "target": schema}


class BatchDoc(BaseModel):
    doc_id: str | None = None
    records: list[dict] = []
    descriptions: dict[str, str] = {}


class BatchSubmit(BaseModel):
    docs: list[BatchDoc]
    research: bool | None = None  # default: whatever Bedrock enablement allows


@app.post("/projects/{project_id}/alignment/batch")
def submit_batch(project_id: str, body: BatchSubmit) -> dict:
    """Queue a mass JSON->golden conversion for this project. Returns a job_id;
    poll GET /jobs/{job_id} for the per-cluster summary + outcomes. The project's
    reject_below dial and stored golden schema + profile library apply."""
    proj = _resolve_project(project_id)
    from .jobs.runtime import get_queue
    from .json_alignment.project_store import load_target_schema

    if load_target_schema(project_id) is None:
        raise HTTPException(400, "set a golden target schema before submitting a batch")
    if not body.docs:
        raise HTTPException(400, "batch has no documents")

    payload = {
        "project_id": project_id,
        "reject_below": proj.reject_below,
        "docs": [d.model_dump() for d in body.docs],
    }
    if body.research is not None:
        payload["research"] = body.research
    job_id = get_queue().enqueue("batch_align", payload)
    return {"job_id": job_id, "state": "pending", "documents": len(body.docs)}


@app.get("/projects/{project_id}/alignment/library")
def get_library(project_id: str) -> dict:
    """The project's learned shapes: each cluster/profile with its approval
    state, so the UI can show per-cluster approval cards."""
    _resolve_project(project_id)
    from .json_alignment.project_store import load_project_library

    lib = load_project_library(project_id)
    entries = []
    for pid in sorted(lib.entries):
        e = lib.entries[pid]
        entries.append({
            "profile_id": e.profile_id,
            "state": e.state,
            "version": e.version,
            "mapped": e.profile.mapping.mapped(),
            "needs_review": e.profile.mapping.needs_review(),
            "conflicts": e.profile.mapping.conflicts(),
        })
    return {
        "project_id": project_id,
        "target_title": lib.target_title,
        "approved": lib.approved_ids(),
        "provisional": lib.provisional_ids(),
        "entries": entries,
    }


class ApproveProfile(BaseModel):
    profile_id: str


@app.post("/projects/{project_id}/alignment/approve")
def approve_profile(project_id: str, body: ApproveProfile) -> dict:
    """Approve a provisional profile (one-time human sign-off for a novel/drifted
    shape). After approval, that shape replays deterministically in future
    batches with zero re-inference."""
    _resolve_project(project_id)
    from .json_alignment.project_store import (
        load_project_library,
        save_project_library,
    )

    lib = load_project_library(project_id)
    if lib.get(body.profile_id) is None:
        raise HTTPException(404, f"no such profile: {body.profile_id}")
    lib.approve(body.profile_id)
    save_project_library(project_id, lib)
    return {"project_id": project_id, "approved": body.profile_id,
            "state": "approved"}


def _components(project_id: str = project.DEFAULT_PROJECT) -> list[dict]:
    return project.component_overview(project_id)


def _component(
    component_id: str,
    mode: str = "draft",
    project_id: str = project.DEFAULT_PROJECT,
    source_format: str = "json",
) -> dict:
    try:
        return {
            "component_id": component_id,
            "mode": mode,
            "project_id": project_id,
            "source_format": source_format,
            "data": project.raw_component(component_id, mode, project_id, source_format),
        }
    except KeyError:
        raise HTTPException(404, f"unknown component: {component_id}")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


def _reconcile(
    mode: str = "draft", project_id: str = project.DEFAULT_PROJECT,
    source_format: str = "json", engine: str = "orchestrator"
) -> dict:
    if mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    if source_format not in ("json", "docx", "pptx", "pdf"):
        raise HTTPException(400, "source_format must be json|docx|pptx|pdf")
    # "coordinator" is the legacy alias for "orchestrator" (kept for back-compat).
    if engine not in ("direct", "orchestrator", "coordinator"):
        raise HTTPException(400, "engine must be 'direct' or 'orchestrator'")
    try:
        return project.run_reconciliation(mode, project_id, source_format, engine=engine)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


class ResolveRequest(BaseModel):
    target: str
    value: str
    mode: str = "draft"
    project_id: str = project.DEFAULT_PROJECT
    source_format: str = "json"
    author: str = "reviewer"


class GenerateRequest(BaseModel):
    domain: str = ""
    doc_type: str = "incident report"
    title: str = ""
    model: str | None = None  # optional approved model override
    dry_run: bool = False  # preview the spec without writing it to disk


class GenerateFromTextRequest(BaseModel):
    text: str  # freeform description; the LLM uses judgment
    model: str | None = None
    dry_run: bool = False


def _generate_project(brief_kwargs: dict, dry_run: bool, model: str | None) -> dict:
    from .projectgen.bedrock_gen import ModelNotApprovedError
    from .projectgen.generator import (
        ProjectBrief,
        get_generator_for_brief,
    )
    from .projectgen.metrics import PRICE_META
    from .projectgen.persist import next_project_id, persist_spec
    from .projectgen.schema import validate_spec

    brief = ProjectBrief(**brief_kwargs)
    try:
        # Corpus-bearing briefs route to the deterministic corpus generator; the
        # model/offline path is used only when no corpus is provided.
        gen = get_generator_for_brief(brief, model_id=model)
    except ModelNotApprovedError as exc:
        raise HTTPException(400, str(exc))
    # Capture per-run score+cost metrics when the generator supports it.
    metrics = None
    if hasattr(gen, "generate_with_metrics"):
        result = gen.generate_with_metrics(brief)
        spec, metrics = result.spec, result.metrics.as_dict()
    else:
        spec = gen.generate(brief)

    problems = validate_spec(spec)
    if problems:
        raise HTTPException(422, "generated project failed validation: " + "; ".join(problems))

    out: dict = {"generator": gen.name, "dry_run": dry_run}
    if metrics is not None:
        out["metrics"] = metrics
        out["price_note"] = PRICE_META
    if dry_run:
        out.update({"project_id": None, "spec": spec.model_dump()})
    else:
        sid = persist_spec(spec, project_id=next_project_id())
        out.update({"project_id": sid, "title": spec.title, "domain": spec.domain})
    return out


@app.get("/generate/models")
def project_models() -> dict:
    """List the live, approved models available for project generation (plus the
    current default and the allowlist). Degrades gracefully offline: `available`
    is false and `models` is empty, so the UI offers only the offline generator."""
    from .projectgen.bedrock_gen import list_approved_models

    return list_approved_models()


@app.post("/generate")
def project_generate(body: GenerateRequest) -> dict:
    """Generate a NEW project from a structured brief (domain + document type).
    Uses the configured generator (deterministic offline by default; an approved
    Bedrock model when enabled or when `model` is given). The output is validated
    and persisted as a new project id, returning per-run score+cost metrics."""
    return _generate_project(
        {"domain": body.domain, "doc_type": body.doc_type, "title": body.title},
        body.dry_run,
        body.model,
    )


@app.post("/generate/from-text")
def project_generate_from_text(body: GenerateFromTextRequest) -> dict:
    """Generate a project on the fly from a freeform description, letting the
    model use its best judgment to choose sections, fields, figures, a table,
    and realistic defects. Falls back to the deterministic generator offline."""
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    return _generate_project({"freeform": body.text}, body.dry_run, body.model)


@app.post("/generate/from-document")
async def project_generate_from_document(
    file: UploadFile = File(...),
    domain: str = "",
    title: str = "",
    dry_run: bool = False,
) -> dict:
    """Generate a project FROM an uploaded document: the document's own text
    becomes the project's ground-truth corpus, and a deterministic
    corpus-grounded generator builds the project around those real facts (no
    model invents anything, so the result is faithful and reproducible). This is
    the core intended flow of the app."""
    from .projectgen.corpus_intake import corpus_from_upload

    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"file exceeds {settings.MAX_UPLOAD_MB} MB")
    try:
        corpus = corpus_from_upload(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    out = _generate_project(
        {
            "domain": domain,
            "title": title,
            "corpus": [{"name": d.name, "text": d.text} for d in corpus],
        },
        dry_run,
        model=None,
    )
    out["corpus_docs"] = [{"name": d.name, "chars": len(d.text)} for d in corpus]
    return out


def _governed_event_stream(brief_kwargs: dict, model: str | None, adjudicator: str):
    """Run the governor on a worker thread and yield its progress events as they
    happen (Server-Sent Events). The final event carries the run summary. Any
    failure degrades to an error event rather than a broken stream."""
    import json
    import queue
    import threading

    from .projectgen.generator import ProjectBrief
    from .projectgen.governor import run_governed

    q: queue.Queue = queue.Queue()
    _DONE = object()

    def on_event(ev) -> None:
        q.put(("event", ev.as_dict()))

    def worker() -> None:
        try:
            author, author_model, adj = _build_governor_parts(model, adjudicator)
            brief = ProjectBrief(**brief_kwargs)
            res = run_governed(
                brief, author=author, adjudicator=adj, author_model=author_model, on_event=on_event
            )
            q.put(("result", res.summary()))
        except Exception as exc:  # never break the stream
            q.put(("error", {"detail": str(exc)[:300]}))
        finally:
            q.put((_DONE, None))

    threading.Thread(target=worker, daemon=True).start()

    def sse() -> Iterator[str]:
        while True:
            kind, data = q.get()
            if kind is _DONE:
                return
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    return sse()


def _build_governor_parts(model: str | None, adjudicator: str):
    """Resolve (author, author_model, adjudicator) for a governed run. Offline by
    default; a Bedrock author + model-based adjudicator only when a model is
    given and approved. Always degrades to the deterministic path on any error."""
    from .projectgen.governor import make_adjudicator

    if not model:
        from .projectgen.rule_generator import RuleProjectGenerator

        return RuleProjectGenerator(), "offline", make_adjudicator(adjudicator)
    try:
        import boto3

        from .projectgen.bedrock_gen import BedrockProjectGenerator
        from .projectgen.model_adapters import adapter_for

        author = BedrockProjectGenerator(model_id=model)
        client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        adj = make_adjudicator(
            adjudicator,
            model_id=author.model_id,
            client=client,
            adapter=adapter_for(author.model_id),
        )
        return author, author.model_id, adj
    except Exception:
        from .projectgen.rule_generator import RuleProjectGenerator

        return RuleProjectGenerator(), "offline", make_adjudicator("deterministic")


@app.get("/generate/governed/stream")
def project_governed_stream(
    domain: str = "",
    doc_type: str = "incident report",
    title: str = "",
    freeform: str = "",
    model: str | None = None,
    adjudicator: str = "deterministic",
) -> StreamingResponse:
    """Run the governed (decomposed) generation and STREAM its progress as the
    operations transpire: one Server-Sent Event per governor step (plan, fill,
    proofread, reconcile), then a final `result` event with the run summary. The
    client renders a live cumulative log. Offline by default (deterministic
    author + adjudicator); pass an approved `model` for the live path."""
    brief_kwargs = (
        {"freeform": freeform}
        if freeform.strip()
        else {"domain": domain, "doc_type": doc_type, "title": title}
    )
    return StreamingResponse(
        _governed_event_stream(brief_kwargs, model, adjudicator),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve(body: ResolveRequest) -> dict:
    """Apply a human decision to one unresolved unit (a conflict or a
    needs_review field): record the chosen/entered value as a new correction
    round and return the re-reconciled report. The rest of the report is
    unchanged; never fabricates (a value is required)."""
    if body.mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    try:
        return project.resolve_unit(
            target=body.target,
            value=body.value,
            mode=body.mode,
            project_id=body.project_id,
            source_format=body.source_format,
            author=body.author,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


def _converge(
    mode: str = "draft", project_id: str = project.DEFAULT_PROJECT, source_format: str = "json"
) -> dict:
    """Run multi-round correction convergence: returns the per-round trajectory
    (unresolved = needs_review + conflict) and whether it converged."""
    if mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    try:
        return project.run_convergence(mode, project_id, source_format)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


class InterpretRequest(BaseModel):
    feedback: str
    project_id: str = project.DEFAULT_PROJECT
    round: int = 0
    author: str = "interpreter"
    apply: bool = False  # if true, run the proposed ops as a correction round


def _interpret(body: InterpretRequest) -> dict:
    """Turn freeform feedback into constrained correction operations (optionally
    Bedrock-backed). Proposes + validates ops; if `apply`, also reconciles the
    draft with those ops as a correction round and returns the resulting report.
    Never invents values or writes final state directly."""
    from .corrections.interpreter import interpret_feedback
    from .corrections.schema import to_engine_correction
    from .reconcile import reconcile

    manifest = project.load_manifest(body.project_id)
    template = project.load_template(body.project_id)
    ctx = {
        "fields": manifest.get("fields", []),
        "section_bodies": manifest.get("section_bodies", {}),
        "sections": [s["key"] for s in template["required_sections"]],
        "graphic_sections": [
            s["key"] for s in template["required_sections"] if s.get("requires_graphic")
        ],
        "table_section": (manifest.get("table") or {}).get("section"),
    }
    result = interpret_feedback(body.feedback, ctx, body.round, body.author)

    if body.apply and result["accepted"]:
        from .corrections.refine import ground_graphic_relabels

        corrections = [to_engine_correction(op) for op in result["accepted"]]
        graphics = project.load_graphics(body.project_id)
        # Ground any prose figure-relabel intent in the submitted feedback to the
        # exact corpus filename (same deterministic step the main pipeline uses),
        # so a freeform "the Timeline chart is wrong" resolves correctly here too.
        corrections = ground_graphic_relabels(
            corrections, graphics=graphics, template=template, feedback_texts=[body.feedback]
        )
        report = reconcile(
            first_attempt=project.load_first_attempt(body.project_id, "draft"),
            corpus=project.load_corpus(body.project_id),
            graphics_manifest=graphics,
            corrections=corrections,
            template=template,
            project=manifest,
        )
        result["applied_report"] = report.model_dump()
    return result


async def _convert(project_id: str, mode: str = "draft", file: UploadFile = File(...)) -> dict:
    """Convert a client-uploaded real document (docx/pptx/pdf) into the internal
    first_attempt shape, judged against the given project's template. This is
    the production path: clients submit files, not JSON."""
    from .convert import convert_document

    data = await file.read()
    try:
        template = project.load_template(project_id)
        manifest = project.load_manifest(project_id)
        result = convert_document(data, file.filename or "upload", template, manifest, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "fidelity": result.fidelity,
        "source_format": result.source_format,
        "notes": result.notes,
        "first_attempt": result.first_attempt,
    }


# --------------------------------------------------------------------------
# Unified project-scoped project API (project = project). These nest the
# project operations under /projects/{project_id}/... and delegate to the same
# handlers above; the flat /project/* routes remain as DEPRECATED ALIASES
# during the migration. Responses are identical.
# --------------------------------------------------------------------------


@app.get("/projects/{project_id}/components")
def project_components(project_id: str) -> list[dict]:
    return _components(project_id=project_id)


@app.get("/projects/{project_id}/component/{component_id}")
def project_component(
    project_id: str, component_id: str, mode: str = "draft", source_format: str = "json"
) -> dict:
    return _component(component_id, mode=mode, project_id=project_id, source_format=source_format)


@app.get("/projects/{project_id}/reconcile")
def project_reconcile(project_id: str, mode: str = "draft", source_format: str = "json",
                      engine: str = "orchestrator") -> dict:
    return _reconcile(mode=mode, project_id=project_id, source_format=source_format,
                      engine=engine)


@app.get("/projects/{project_id}/converge")
def project_converge(project_id: str, mode: str = "draft", source_format: str = "json") -> dict:
    return _converge(mode=mode, project_id=project_id, source_format=source_format)


@app.post("/projects/{project_id}/resolve")
def project_resolve(project_id: str, body: ResolveRequest) -> dict:
    body.project_id = project_id  # path wins over any body value
    return _resolve(body)


@app.post("/projects/{project_id}/interpret")
def project_interpret(project_id: str, body: InterpretRequest) -> dict:
    body.project_id = project_id
    return _interpret(body)


@app.post("/projects/{project_id}/convert")
async def project_convert(
    project_id: str, mode: str = "draft", file: UploadFile = File(...)
) -> dict:
    return await _convert(project_id=project_id, mode=mode, file=file)
