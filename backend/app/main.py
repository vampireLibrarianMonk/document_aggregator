"""FastAPI surface for the document aggregation platform.

Barebones but complete: projects, document upload + pipeline status, canonical
JSON, supplementals (comments / emails / corrections), aggregated intermediate
report, and export to json/markdown/docx/pptx/pdf.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
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
        raise HTTPException(404, "diagnostics is disabled "
                                 "(set DIAGNOSTICS_ENABLED=true to enable)")
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
    return store.create_project(
        Project(id=pid, name=body.name, description=body.description))


@app.get("/projects")
def list_projects() -> list[Project]:
    """The live project list: ONLY projects the user has actually created in the
    store (by uploading documents, generating, or instantiating a sample case).
    The app starts EMPTY. The bundled sample cases are NOT auto-listed here; they
    are templates surfaced via GET /templates and instantiated on demand."""
    return store.list_projects()


class InstantiateTemplate(BaseModel):
    name: str | None = None   # optional override for the new project's name


def _require_samples() -> None:
    """Guard: the in-app sample feature is opt-in (SAMPLES_ENABLED). When off,
    the sample endpoints behave as if they do not exist, so the feature is fully
    gated server-side, not just hidden in the UI."""
    if not settings.SAMPLES_ENABLED:
        raise HTTPException(404, "sample instantiation is disabled "
                                 "(set SAMPLES_ENABLED=true to enable)")


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

@app.post("/projects/{project_id}/documents")
async def upload_document(project_id: str, file: UploadFile = File(...)) -> dict:
    _resolve_project(project_id)
    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"file exceeds {settings.MAX_UPLOAD_MB} MB")
    rec = ingest_document(project_id, file.filename or "unnamed", data)
    return {"document_id": rec.id, "overall_status": rec.overall_status,
            "stages": [s.model_dump() for s in rec.stages]}


@app.get("/projects/{project_id}/documents")
def list_documents(project_id: str) -> list[dict]:
    _resolve_project(project_id)
    out = []
    for r in store.list_records(project_id):
        d = r.model_dump()
        d["overall_status"] = r.overall_status
        out.append(d)
    return out


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
        kind=body.kind, author=body.author, subject=body.subject, body=body.body,
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
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
        "job_id": job.id, "op": job.op, "state": job.state.value,
        "attempts": job.attempts, "error": job.error, "result": job.result,
    }


@app.get("/jobs")
def job_counts() -> dict:
    from .jobs.runtime import get_queue

    return {"counts": get_queue().counts()}


def _components(project_id: str = project.DEFAULT_PROJECT) -> list[dict]:
    return project.component_overview(project_id)


def _component(component_id: str, mode: str = "draft",
               project_id: str = project.DEFAULT_PROJECT,
               source_format: str = "json") -> dict:
    try:
        return {"component_id": component_id, "mode": mode, "project_id": project_id,
                "source_format": source_format,
                "data": project.raw_component(component_id, mode, project_id, source_format)}
    except KeyError:
        raise HTTPException(404, f"unknown component: {component_id}")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


def _reconcile(mode: str = "draft",
               project_id: str = project.DEFAULT_PROJECT,
               source_format: str = "json") -> dict:
    if mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    if source_format not in ("json", "docx", "pptx", "pdf"):
        raise HTTPException(400, "source_format must be json|docx|pptx|pdf")
    try:
        return project.run_reconciliation(mode, project_id, source_format)
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
    model: str | None = None       # optional approved model override
    dry_run: bool = False          # preview the spec without writing it to disk


class GenerateFromTextRequest(BaseModel):
    text: str                      # freeform description; the LLM uses judgment
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
        body.dry_run, body.model,
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
        {"domain": domain, "title": title,
         "corpus": [{"name": d.name, "text": d.text} for d in corpus]},
        dry_run, model=None,
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
            res = run_governed(brief, author=author, adjudicator=adj,
                               author_model=author_model, on_event=on_event)
            q.put(("result", res.summary()))
        except Exception as exc:  # never break the stream
            q.put(("error", {"detail": str(exc)[:300]}))
        finally:
            q.put((_DONE, None))

    threading.Thread(target=worker, daemon=True).start()

    def sse() -> str:
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
        adj = make_adjudicator(adjudicator, model_id=author.model_id, client=client,
                               adapter=adapter_for(author.model_id))
        return author, author.model_id, adj
    except Exception:
        from .projectgen.rule_generator import RuleProjectGenerator
        return RuleProjectGenerator(), "offline", make_adjudicator("deterministic")


@app.get("/generate/governed/stream")
def project_governed_stream(domain: str = "", doc_type: str = "incident report",
                             title: str = "", freeform: str = "",
                             model: str | None = None,
                             adjudicator: str = "deterministic") -> StreamingResponse:
    """Run the governed (decomposed) generation and STREAM its progress as the
    operations transpire: one Server-Sent Event per governor step (plan, fill,
    proofread, reconcile), then a final `result` event with the run summary. The
    client renders a live cumulative log. Offline by default (deterministic
    author + adjudicator); pass an approved `model` for the live path."""
    brief_kwargs = ({"freeform": freeform} if freeform.strip()
                    else {"domain": domain, "doc_type": doc_type, "title": title})
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
            target=body.target, value=body.value, mode=body.mode,
            project_id=body.project_id, source_format=body.source_format,
            author=body.author,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


def _converge(mode: str = "draft",
              project_id: str = project.DEFAULT_PROJECT,
              source_format: str = "json") -> dict:
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
        "graphic_sections": [s["key"] for s in template["required_sections"]
                             if s.get("requires_graphic")],
        "table_section": (manifest.get("table") or {}).get("section"),
    }
    result = interpret_feedback(body.feedback, ctx, body.round, body.author)

    if body.apply and result["accepted"]:
        corrections = [to_engine_correction(op) for op in result["accepted"]]
        report = reconcile(
            first_attempt=project.load_first_attempt(body.project_id, "draft"),
            corpus=project.load_corpus(body.project_id),
            graphics_manifest=project.load_graphics(body.project_id),
            corrections=corrections,
            template=project.load_template(body.project_id),
            project=manifest,
        )
        result["applied_report"] = report.model_dump()
    return result


async def _convert(project_id: str, mode: str = "draft",
                   file: UploadFile = File(...)) -> dict:
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
    return {"fidelity": result.fidelity, "source_format": result.source_format,
            "notes": result.notes, "first_attempt": result.first_attempt}


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
def project_component(project_id: str, component_id: str, mode: str = "draft",
                      source_format: str = "json") -> dict:
    return _component(component_id, mode=mode, project_id=project_id,
                      source_format=source_format)


@app.get("/projects/{project_id}/reconcile")
def project_reconcile(project_id: str, mode: str = "draft",
                      source_format: str = "json") -> dict:
    return _reconcile(mode=mode, project_id=project_id, source_format=source_format)


@app.get("/projects/{project_id}/converge")
def project_converge(project_id: str, mode: str = "draft",
                     source_format: str = "json") -> dict:
    return _converge(mode=mode, project_id=project_id, source_format=source_format)


@app.post("/projects/{project_id}/resolve")
def project_resolve(project_id: str, body: ResolveRequest) -> dict:
    body.project_id = project_id          # path wins over any body value
    return _resolve(body)


@app.post("/projects/{project_id}/interpret")
def project_interpret(project_id: str, body: InterpretRequest) -> dict:
    body.project_id = project_id
    return _interpret(body)


@app.post("/projects/{project_id}/convert")
async def project_convert(project_id: str, mode: str = "draft",
                          file: UploadFile = File(...)) -> dict:
    return await _convert(project_id=project_id, mode=mode, file=file)
