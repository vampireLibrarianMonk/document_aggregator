"""Project service: loads a numbered project's four components and runs
reconciliation. Projects live in sample_docs/project/<id>/ and are fully
data-driven via project.json, so the engine stays generic.

Layout per project:
    project/<id>/project.json           manifest (fields, queries, table spec)
    project/<id>/corpus/*.txt|*.md       source docs (ground truth)
    project/<id>/corpus/graphics.json    named graphic references
    project/<id>/first_attempt/incident_report_draft.json
    project/<id>/first_attempt/incident_report_template.json
    project/<id>/corrections/comments.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import settings
from .reconcile import reconcile

# Single source of truth lives in config; re-exported here for existing callers.
BUNDLED_PROJECT_ROOT = settings.BUNDLED_PROJECT_ROOT
DEFAULT_PROJECT = "1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dir(project_id: str) -> Path:
    """Resolve a project's directory, preferring the unified project store and
    falling back to the legacy bundled sample_docs/project/<id>. All loaders go
    through here, so relocating storage required no loader changes."""
    return settings.resolve_project_data_dir(project_id)


def list_templates() -> list[dict]:
    """The catalog of bundled sample CASES a user can instantiate. This reads
    ONLY the committed read-only bundle (sample_docs/project/<id>), never the
    store, so instantiated copies never appear here as duplicate 'templates'."""
    out: list[dict] = []
    root = settings.BUNDLED_PROJECT_ROOT
    if root.exists():
        for d in sorted(root.iterdir()):
            manifest = d / "project.json"
            if manifest.exists():
                m = _read_json(manifest)
                out.append({"id": m.get("id", d.name), "title": m.get("title", d.name),
                            "domain": m.get("domain", "")})
    return out


def list_project_cases() -> list[dict]:
    """Every resolvable case from BOTH the store and the bundle (store wins on
    id collision). Used by tests to enumerate loadable cases. For the user-facing
    template CATALOG use list_templates() (bundle-only)."""
    out: list[dict] = []
    for sid, d in settings.iter_project_data_dirs():
        m = _read_json(d / "project.json")
        out.append({"id": m.get("id", sid), "title": m.get("title", sid),
                    "domain": m.get("domain", "")})
    return out


def load_manifest(project_id: str) -> dict:
    return _read_json(_dir(project_id) / "project.json")


def load_corpus(project_id: str) -> dict[str, str]:
    cdir = _dir(project_id) / "corpus"
    if not cdir.is_dir():
        return {}
    return {p.name: _read_text(p) for p in sorted(cdir.iterdir()) if p.suffix in (".txt", ".md")}


def load_graphics(project_id: str) -> list[dict]:
    path = _dir(project_id) / "corpus" / "graphics.json"
    if not path.exists():
        return []
    return _read_json(path)["graphics"]


def load_corrections(project_id: str, variant: str = "comments") -> list[dict]:
    """variant='comments' = single-round review (default); 'rounds' = the
    multi-round convergence feedback if the project provides it."""
    fname = "rounds.json" if variant == "rounds" else "comments.json"
    path = _dir(project_id) / "corrections" / fname
    if not path.exists() and variant == "rounds":
        path = _dir(project_id) / "corrections" / "comments.json"
    if not path.exists():
        return []
    return _read_json(path)["corrections"]


def has_rounds(project_id: str) -> bool:
    return (_dir(project_id) / "corrections" / "rounds.json").exists()


def run_convergence(mode: str = "draft", project_id: str = DEFAULT_PROJECT,
                    source_format: str | None = None) -> dict:
    from .reconcile import converge

    return converge(
        first_attempt=_first_attempt_for(project_id, mode, source_format),
        corpus=load_corpus(project_id),
        graphics_manifest=load_graphics(project_id),
        corrections=load_corrections(project_id, "rounds"),
        template=load_template(project_id),
        project=load_manifest(project_id),
    )


def load_first_attempt(project_id: str, mode: str) -> dict:
    fname = "incident_report_template.json" if mode == "template" else "incident_report_draft.json"
    return _read_json(_dir(project_id) / "first_attempt" / fname)


def load_first_attempt_from_document(project_id: str, mode: str, source_format: str) -> dict:
    """Convert a generated real document (docx/pptx/pdf) into the first_attempt
    shape, exactly as a client-submitted file would be handled. Returns a dict
    with an added `_conversion` block recording fidelity/format/notes."""
    from .convert import convert_document

    gen = _dir(project_id) / "first_attempt" / "generated" / f"{mode}.{source_format}"
    if not gen.exists():
        raise FileNotFoundError(f"no generated {source_format} for project {project_id} {mode}")
    template = load_template(project_id)
    manifest = load_manifest(project_id)
    result = convert_document(gen.read_bytes(), gen.name, template, manifest, mode)
    fa = result.first_attempt
    fa["_conversion"] = {"fidelity": result.fidelity, "source_format": result.source_format,
                         "notes": result.notes}
    return fa


def load_template(project_id: str) -> dict:
    return _read_json(_dir(project_id) / "first_attempt" / "incident_report_template.json")


def load_template_evidence(project_id: str) -> dict | None:
    """Extract the TEMPLATE document's own observable evidence (from template.docx)
    so the build-discipline rubric can be divined from the template rather than
    a hand-authored JSON. Returns the `_evidence` block, or None if no template
    document exists (falls back to the JSON/profile rubric)."""
    gen = _dir(project_id) / "first_attempt" / "generated" / "template.docx"
    if not gen.exists():
        return None
    from .convert import convert_document
    template = load_template(project_id)
    manifest = load_manifest(project_id)
    try:
        result = convert_document(gen.read_bytes(), gen.name, template, manifest, "template")
    except Exception:
        return None
    return result.first_attempt.get("_evidence")


def has_correction_pipeline(project_id: str) -> bool:
    """True when a project carries correction-pipeline fixtures (a first_attempt
    draft/template). Aggregation-only projects (documents, supplementals, index
    but no first_attempt) return False so callers can skip the pipeline cleanly
    instead of 500ing on absent corpus/corrections dirs."""
    fa = _dir(project_id) / "first_attempt"
    return (fa / "incident_report_draft.json").exists() or \
           (fa / "incident_report_template.json").exists()


def component_overview(project_id: str = DEFAULT_PROJECT) -> list[dict]:
    corpus = load_corpus(project_id)
    graphics = load_graphics(project_id)
    corrections = load_corrections(project_id)
    has_draft = (_dir(project_id) / "first_attempt" / "incident_report_draft.json").exists()
    has_template = (_dir(project_id) / "first_attempt" / "incident_report_template.json").exists()
    first_attempt_items = []
    if has_draft:
        first_attempt_items.append(
            {"name": "incident_report_draft.json", "kind": "draft", "note": "completed but flawed"})
    if has_template:
        first_attempt_items.append(
            {"name": "incident_report_template.json", "kind": "template",
             "note": "machine-readable rubric"})
    return [
        {
            "id": "corpus", "order": 1, "title": "Original corpus",
            "subtitle": "Raw source documents (ground truth)",
            "items": [
                *[{"name": n, "kind": "document", "chars": len(t)} for n, t in corpus.items()],
                *[{"name": g["name"], "kind": "graphic", "caption": g["caption"]} for g in graphics],
            ],
        },
        {
            "id": "first_attempt", "order": 2, "title": "First attempt",
            "subtitle": "Draft deliverable or blank template (the thing found to be wrong)",
            "items": first_attempt_items,
        },
        {
            "id": "corrections", "order": 3, "title": "Comments / emails",
            "subtitle": "Human feedback: what is wrong (the judge, in draft mode)",
            "items": [
                {"name": c["id"], "kind": c["kind"], "author": c["author"],
                 "subject": c["subject"], "target": c["target"]}
                for c in corrections
            ],
        },
        {
            "id": "intermediate_json", "order": 4, "title": "Corrected intermediate JSON",
            "subtitle": "Output-neutral corrected representation (the stopping point)",
            "items": [],
        },
    ]


def _first_attempt_for(project_id: str, mode: str, source_format: str | None) -> dict:
    """JSON baseline when source_format is None/'json', else convert a real doc."""
    if source_format in (None, "json"):
        return load_first_attempt(project_id, mode)
    return load_first_attempt_from_document(project_id, mode, source_format)


def run_reconciliation(mode: str = "draft", project_id: str = DEFAULT_PROJECT,
                       source_format: str | None = None,
                       extra_corrections: list[dict] | None = None) -> dict:
    template = load_template(project_id)
    # When the source is a real document, divine the discipline rubric from the
    # template DOCUMENT's own formatting (attach its evidence for the engine).
    if source_format in ("docx", "pdf"):
        tev = load_template_evidence(project_id)
        if tev:
            template = {**template, "_evidence": tev}
    # The project's own corrections plus any ad-hoc human decisions (e.g. a
    # reviewer resolving a conflict or supplying a needs_review value). The
    # engine's last-good-wins round collapse lets a later-round extra correction
    # supersede the original conflicting ones for that target.
    corrections = load_corrections(project_id)
    if extra_corrections:
        corrections = corrections + list(extra_corrections)
    report = reconcile(
        first_attempt=_first_attempt_for(project_id, mode, source_format),
        corpus=load_corpus(project_id),
        graphics_manifest=load_graphics(project_id),
        corrections=corrections,
        template=template,
        project=load_manifest(project_id),
    )
    result = report.model_dump()
    # Vector-layout tier (gated): when the source is a real document, render to
    # PDF and inspect element geometry, merging findings. Degrades silently if
    # LibreOffice is absent (structural inspection only).
    if source_format in ("docx", "pdf"):
        vec = _vector_findings(project_id, mode, source_format, template)
        if vec:
            result["discipline_findings"] = result.get("discipline_findings", []) + vec
            result["vector_tier_ran"] = True
    return result


def resolvable_targets(project_id: str = DEFAULT_PROJECT) -> list[str]:
    """The set of unit targets a human may resolve (fields, section bodies,
    graphic sections, the table, and furniture elements)."""
    from .corrections.interpreter import _valid_targets

    manifest = load_manifest(project_id)
    template = load_template(project_id)
    ctx = {
        "fields": manifest.get("fields", []),
        "section_bodies": manifest.get("section_bodies", {}),
        "sections": [s["key"] for s in template["required_sections"]],
        "graphic_sections": [s["key"] for s in template["required_sections"]
                             if s.get("requires_graphic")],
        "table_section": (manifest.get("table") or {}).get("section"),
    }
    return _valid_targets(ctx)


def resolve_unit(target: str, value: str | None, mode: str = "draft",
                 project_id: str = DEFAULT_PROJECT, source_format: str | None = None,
                 author: str = "reviewer") -> dict:
    """Apply a human decision to a single unit: supply a chosen/entered value for
    a conflicted or needs_review target. The decision is injected as a NEW
    correction round (max existing round + 1), so the engine's last-good-wins
    collapse supersedes any earlier conflicting corrections for that target while
    leaving every other unit's resolution untouched. Never fabricates: a value is
    required (there is no 'resolve to nothing').
    """
    from .reconcile.engine import max_round

    if target not in resolvable_targets(project_id):
        raise ValueError(f"'{target}' is not a resolvable unit for project {project_id}")
    if value is None or str(value).strip() == "":
        raise ValueError("a non-empty value is required to resolve a unit (no fabrication)")

    base = load_corrections(project_id)
    next_round = max_round(base) + 1
    decision = {
        "id": f"resolve_{target.replace('.', '_')}_r{next_round}",
        "round": next_round,
        "kind": "resolution",
        "author": author,
        "subject": f"human resolution of {target}",
        "target": target,
        "operation": "replace",
        "new_value": str(value),
        "body": f"Resolved by {author}.",
    }
    return run_reconciliation(mode, project_id, source_format, extra_corrections=[decision])


def _vector_findings(project_id: str, mode: str, source_format: str, template: dict) -> list[dict]:
    from .discipline import load_discipline
    from .discipline.vector import inspect_vector_layout

    gen = _dir(project_id) / "first_attempt" / "generated" / f"{mode}.{source_format}"
    if not gen.exists():
        return []
    discipline = load_discipline(template)
    findings, ran = inspect_vector_layout(gen.read_bytes(), source_format, discipline)
    return [f.model_dump() for f in findings] if ran else []


def raw_component(component_id: str, mode: str = "draft", project_id: str = DEFAULT_PROJECT,
                  source_format: str | None = None) -> Any:
    if component_id == "corpus":
        return {"documents": load_corpus(project_id), "graphics": load_graphics(project_id)}
    if component_id == "first_attempt":
        return _first_attempt_for(project_id, mode, source_format)
    if component_id == "corrections":
        return load_corrections(project_id)
    if component_id == "intermediate_json":
        return run_reconciliation(mode, project_id, source_format)
    raise KeyError(component_id)
