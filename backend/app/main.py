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


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

class CreateProject(BaseModel):
    name: str


@app.post("/projects")
def create_project(body: CreateProject) -> Project:
    pid = "proj_" + uuid.uuid4().hex[:10]
    return store.create_project(Project(id=pid, name=body.name))


@app.get("/projects")
def list_projects() -> list[Project]:
    """Unified project list: every stored Project PLUS every scenario in either
    store (a project IS a scenario). Scenario entries that are not yet backed by
    a project record are surfaced with a synthetic record so the single global
    selector has one clean source. Stored projects win on an id collision."""
    out: list[Project] = []
    seen: set[str] = set()
    for p in store.list_projects():
        out.append(p)
        seen.add(p.id)
    for s in scenario.list_scenarios():
        sid = str(s["id"])
        if sid not in seen:
            out.append(Project(id=sid, name=s.get("title") or sid))
            seen.add(sid)
    return out


def _resolve_project(project_id: str) -> Project:
    """Return the project record, materializing one on first use for a scenario
    id that exists in either store (so selecting a demo scenario as the active
    project just works). 404 only if neither a project nor a scenario exists."""
    p = store.get_project(project_id)
    if p:
        return p
    # A scenario that exists but has no project record yet -> adopt it.
    if (settings.resolve_scenario_dir(project_id) / "scenario.json").exists():
        title = project_id
        try:
            title = scenario.load_manifest(project_id).get("title", project_id)
        except Exception:
            pass
        return store.create_project(Project(id=project_id, name=title))
    raise HTTPException(404, "project not found")


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> Project:
    return _resolve_project(project_id)


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
# Correction scenario: the four-component pipeline
#   corpus -> first-attempt (draft|template) -> corrections -> corrected JSON
# --------------------------------------------------------------------------

from . import scenario  # noqa: E402


@app.get("/scenarios")
def scenarios_list() -> list[dict]:
    return scenario.list_scenarios()


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


@app.get("/scenario/components")
def scenario_components(scenario_id: str = scenario.DEFAULT_SCENARIO) -> list[dict]:
    return scenario.component_overview(scenario_id)


@app.get("/scenario/component/{component_id}")
def scenario_component(component_id: str, mode: str = "draft",
                       scenario_id: str = scenario.DEFAULT_SCENARIO,
                       source_format: str = "json") -> dict:
    try:
        return {"component_id": component_id, "mode": mode, "scenario_id": scenario_id,
                "source_format": source_format,
                "data": scenario.raw_component(component_id, mode, scenario_id, source_format)}
    except KeyError:
        raise HTTPException(404, f"unknown component: {component_id}")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/scenario/reconcile")
def scenario_reconcile(mode: str = "draft",
                       scenario_id: str = scenario.DEFAULT_SCENARIO,
                       source_format: str = "json") -> dict:
    if mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    if source_format not in ("json", "docx", "pptx", "pdf"):
        raise HTTPException(400, "source_format must be json|docx|pptx|pdf")
    try:
        return scenario.run_reconciliation(mode, scenario_id, source_format)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


class ResolveRequest(BaseModel):
    target: str
    value: str
    mode: str = "draft"
    scenario_id: str = scenario.DEFAULT_SCENARIO
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


def _generate_scenario(brief_kwargs: dict, dry_run: bool, model: str | None) -> dict:
    from .scenariogen.bedrock_gen import ModelNotApprovedError
    from .scenariogen.generator import (
        ScenarioBrief,
        get_generator_for_brief,
    )
    from .scenariogen.metrics import PRICE_META
    from .scenariogen.persist import next_scenario_id, persist_spec
    from .scenariogen.schema import validate_spec

    brief = ScenarioBrief(**brief_kwargs)
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
        raise HTTPException(422, "generated scenario failed validation: " + "; ".join(problems))

    out: dict = {"generator": gen.name, "dry_run": dry_run}
    if metrics is not None:
        out["metrics"] = metrics
        out["price_note"] = PRICE_META
    if dry_run:
        out.update({"scenario_id": None, "spec": spec.model_dump()})
    else:
        sid = persist_spec(spec, scenario_id=next_scenario_id())
        out.update({"scenario_id": sid, "title": spec.title, "domain": spec.domain})
    return out


@app.get("/scenario/models")
def scenario_models() -> dict:
    """List the live, approved models available for scenario generation (plus the
    current default and the allowlist). Degrades gracefully offline: `available`
    is false and `models` is empty, so the UI offers only the offline generator."""
    from .scenariogen.bedrock_gen import list_approved_models

    return list_approved_models()


@app.post("/scenario/generate")
def scenario_generate(body: GenerateRequest) -> dict:
    """Generate a NEW scenario from a structured brief (domain + document type).
    Uses the configured generator (deterministic offline by default; an approved
    Bedrock model when enabled or when `model` is given). The output is validated
    and persisted as a new scenario id, returning per-run score+cost metrics."""
    return _generate_scenario(
        {"domain": body.domain, "doc_type": body.doc_type, "title": body.title},
        body.dry_run, body.model,
    )


@app.post("/scenario/generate/from-text")
def scenario_generate_from_text(body: GenerateFromTextRequest) -> dict:
    """Generate a scenario on the fly from a freeform description, letting the
    model use its best judgment to choose sections, fields, figures, a table,
    and realistic defects. Falls back to the deterministic generator offline."""
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    return _generate_scenario({"freeform": body.text}, body.dry_run, body.model)


@app.post("/scenario/generate/from-document")
async def scenario_generate_from_document(
    file: UploadFile = File(...),
    domain: str = "",
    title: str = "",
    dry_run: bool = False,
) -> dict:
    """Generate a scenario FROM an uploaded document: the document's own text
    becomes the scenario's ground-truth corpus, and a deterministic
    corpus-grounded generator builds the scenario around those real facts (no
    model invents anything, so the result is faithful and reproducible). This is
    the core intended flow of the app."""
    from .scenariogen.corpus_intake import corpus_from_upload

    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"file exceeds {settings.MAX_UPLOAD_MB} MB")
    try:
        corpus = corpus_from_upload(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    out = _generate_scenario(
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

    from .scenariogen.generator import ScenarioBrief
    from .scenariogen.governor import run_governed

    q: queue.Queue = queue.Queue()
    _DONE = object()

    def on_event(ev) -> None:
        q.put(("event", ev.as_dict()))

    def worker() -> None:
        try:
            author, author_model, adj = _build_governor_parts(model, adjudicator)
            brief = ScenarioBrief(**brief_kwargs)
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
    from .scenariogen.governor import make_adjudicator

    if not model:
        from .scenariogen.rule_generator import RuleScenarioGenerator
        return RuleScenarioGenerator(), "offline", make_adjudicator(adjudicator)
    try:
        import boto3

        from .scenariogen.bedrock_gen import BedrockScenarioGenerator
        from .scenariogen.model_adapters import adapter_for
        author = BedrockScenarioGenerator(model_id=model)
        client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        adj = make_adjudicator(adjudicator, model_id=author.model_id, client=client,
                               adapter=adapter_for(author.model_id))
        return author, author.model_id, adj
    except Exception:
        from .scenariogen.rule_generator import RuleScenarioGenerator
        return RuleScenarioGenerator(), "offline", make_adjudicator("deterministic")


@app.get("/scenario/governed/stream")
def scenario_governed_stream(domain: str = "", doc_type: str = "incident report",
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


@app.post("/scenario/resolve")
def scenario_resolve(body: ResolveRequest) -> dict:
    """Apply a human decision to one unresolved unit (a conflict or a
    needs_review field): record the chosen/entered value as a new correction
    round and return the re-reconciled report. The rest of the report is
    unchanged; never fabricates (a value is required)."""
    if body.mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    try:
        return scenario.resolve_unit(
            target=body.target, value=body.value, mode=body.mode,
            scenario_id=body.scenario_id, source_format=body.source_format,
            author=body.author,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/scenario/converge")
def scenario_converge(mode: str = "draft",
                      scenario_id: str = scenario.DEFAULT_SCENARIO,
                      source_format: str = "json") -> dict:
    """Run multi-round correction convergence: returns the per-round trajectory
    (unresolved = needs_review + conflict) and whether it converged."""
    if mode not in ("draft", "template"):
        raise HTTPException(400, "mode must be 'draft' or 'template'")
    try:
        return scenario.run_convergence(mode, scenario_id, source_format)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


class InterpretRequest(BaseModel):
    feedback: str
    scenario_id: str = scenario.DEFAULT_SCENARIO
    round: int = 0
    author: str = "interpreter"
    apply: bool = False  # if true, run the proposed ops as a correction round


@app.post("/scenario/interpret")
def scenario_interpret(body: InterpretRequest) -> dict:
    """Turn freeform feedback into constrained correction operations (optionally
    Bedrock-backed). Proposes + validates ops; if `apply`, also reconciles the
    draft with those ops as a correction round and returns the resulting report.
    Never invents values or writes final state directly."""
    from .corrections.interpreter import interpret_feedback
    from .corrections.schema import to_engine_correction
    from .reconcile import reconcile

    manifest = scenario.load_manifest(body.scenario_id)
    template = scenario.load_template(body.scenario_id)
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
            first_attempt=scenario.load_first_attempt(body.scenario_id, "draft"),
            corpus=scenario.load_corpus(body.scenario_id),
            graphics_manifest=scenario.load_graphics(body.scenario_id),
            corrections=corrections,
            template=scenario.load_template(body.scenario_id),
            scenario=manifest,
        )
        result["applied_report"] = report.model_dump()
    return result


@app.post("/scenario/convert")
async def scenario_convert(scenario_id: str, mode: str = "draft",
                           file: UploadFile = File(...)) -> dict:
    """Convert a client-uploaded real document (docx/pptx/pdf) into the internal
    first_attempt shape, judged against the given scenario's template. This is
    the production path: clients submit files, not JSON."""
    from .convert import convert_document

    data = await file.read()
    try:
        template = scenario.load_template(scenario_id)
        manifest = scenario.load_manifest(scenario_id)
        result = convert_document(data, file.filename or "upload", template, manifest, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"fidelity": result.fidelity, "source_format": result.source_format,
            "notes": result.notes, "first_attempt": result.first_attempt}


# --------------------------------------------------------------------------
# Unified project-scoped scenario API (project = scenario). These nest the
# scenario operations under /projects/{project_id}/... and delegate to the same
# handlers above; the flat /scenario/* routes remain as DEPRECATED ALIASES
# during the migration. Responses are identical.
# --------------------------------------------------------------------------

@app.get("/projects/{project_id}/scenario/components")
def project_scenario_components(project_id: str) -> list[dict]:
    return scenario_components(scenario_id=project_id)


@app.get("/projects/{project_id}/scenario/component/{component_id}")
def project_scenario_component(project_id: str, component_id: str, mode: str = "draft",
                               source_format: str = "json") -> dict:
    return scenario_component(component_id, mode=mode, scenario_id=project_id,
                              source_format=source_format)


@app.get("/projects/{project_id}/scenario/reconcile")
def project_scenario_reconcile(project_id: str, mode: str = "draft",
                               source_format: str = "json") -> dict:
    return scenario_reconcile(mode=mode, scenario_id=project_id, source_format=source_format)


@app.get("/projects/{project_id}/scenario/converge")
def project_scenario_converge(project_id: str, mode: str = "draft",
                              source_format: str = "json") -> dict:
    return scenario_converge(mode=mode, scenario_id=project_id, source_format=source_format)


@app.post("/projects/{project_id}/scenario/resolve")
def project_scenario_resolve(project_id: str, body: ResolveRequest) -> dict:
    body.scenario_id = project_id          # path wins over any body value
    return scenario_resolve(body)


@app.post("/projects/{project_id}/scenario/interpret")
def project_scenario_interpret(project_id: str, body: InterpretRequest) -> dict:
    body.scenario_id = project_id
    return scenario_interpret(body)


@app.post("/projects/{project_id}/scenario/convert")
async def project_scenario_convert(project_id: str, mode: str = "draft",
                                   file: UploadFile = File(...)) -> dict:
    return await scenario_convert(scenario_id=project_id, mode=mode, file=file)
